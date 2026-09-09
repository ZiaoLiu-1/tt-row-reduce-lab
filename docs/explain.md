# 中文导读：33×33 的行和怎样走过三个 kernel

目标是把 33 行中的每一行加起来，得到 33 个数。输入先舍入到 BF16，
实际上传什么位模式，CPU reference 就对什么求和。比如 `0.1f` 量化后
通常不再等于原值，直接用原始 float 总和比较会混淆输入误差与计算误差。

```text
CPU 量化／补零／tilize
  → 输入 DRAM
  → reader → input DFB ─────────┐
           → scaler DFB ────────┤
                               ↓
                       compute 行求和
                               ↓
                         output DFB
                               ↓
                            writer
                               ↓
                          输出 DRAM
                               ↓
                      CPU untilize／比较
```

33×33 会补成 64×64，因为 tile 是 32×32。四个输入 tile 覆盖左上
32×32、右侧一列、下侧一行和右下一个元素；剩余位置填零。零不会改变
sum。每个 tile 内还分成四个 16×16 face，所以“取连续 1024 个矩阵
元素”不等于正确 tile。读源码时先跟踪 `(0,16)`、`(16,0)` 和 `(32,32)`
三个坐标，观察 face 与 tile 边界。

输出是两个完整 tile，而不是紧凑的 33 元素数组。每个输出 tile 的每行
第零列放行和，其余列清零；补出的行也为零。untilize 后再取 column 0，
才能得到正确的 33 个数。

## 五段关键代码

1. **[`prepare_input`](../include/row_reduce/row_reduce.hpp)：上传之前。**
   输入是 shape 与 float 数组；输出包含逻辑 BF16 位、补零矩阵、tile
   数据和每行 reference。先检查 shape、长度与有限性，量化后再检查支持的
   数值范围并产生 reference；发生拒绝时设备还没有创建。阅读后能解释：为什么
   `N=33` 的最后 31 个物理列不能含旧内存？

2. **[`reader.cpp::kernel_main`](../kernels/reader.cpp)：把数据交给计算。**
   输入是 DRAM tensor 和 tile 数；输出是 input/scaler DFB。scaler 是
   reduction 所需的单位系数布局。每次先预留 DFB 空位，发起异步读取，
   等待 barrier，再 `push_back`。关键决定是用 push 表示“数据已经完整”，
   不能把“读取已经发出”当作“数据可以使用”。

3. **[`compute.cpp::kernel_main`](../kernels/compute.cpp)：一行跨两个 tile。**
   输入是一个 tile-row 中的 `Wt` 个 tile 与 scaler；输出是一个行和
   tile。对 33×33，`Ht=2`、`Wt=2`：先合并左、右两 tile 的对应行，
   再处理下一 tile-row。关键决定是全部宽度累加完才 pack 成 BF16，
   中间使用 FP32 Dest。host flag、compute define 和 reduce 模板必须一致。

4. **[`writer.cpp::kernel_main`](../kernels/writer.cpp)：完成以后再释放。**
   输入是 output DFB；输出是 DRAM 的完整 tile。`wait_front` 等 compute
   发布结果，异步写出后还要等 write barrier，最后才 `pop_front`。
   关键决定是：pop 允许 producer 重用空间，因此不能提前 pop。

5. **[`run`](../src/metal_main.cpp) 与 [`decode_output`](../include/row_reduce/row_reduce.hpp)：读回真实结果。**
   host 保持 mesh/tensor/workload 存活，完整绑定参数，执行并 `Finish`，
   再从 DRAM 读取。每次先用 NaN 覆盖输出，可抓到漏写。untilize 后取
   每行 column 0，并检查其他位置为零；独立 Python checker 再解码原始
   BF16 位并比较。关键决定是从实际 readback 获取 actual，reference
   只用于判断，不能替代设备输出。

host 的 `make_program` 把以上三个 kernel、DFB 和 tensor 绑定到同一个
node。`Ht/Wt/NC` 是编译期参数，不能只修改 runtime args 就假装改变了
这些专门化值。其余参数每轮完整设置。两轮 repeat 保留相同对象，但
上传不同输入，因此可以检验输入更新和输出复用是否正确。

## 一次短演示

先预测：33×33 需要几个输入、输出 tile？全一输入的每个结果应该是多少？
再在配置好的 Linux 环境执行：

```sh
"$TT_METAL_HOME/build_Release/row-reduce-lab/tt_row_reduce_metal" \
  --rows 33 --cols 33 --pattern ones --seed 109 --nodes 1 --repeat 2
```

第一轮期望 33 个 `33`，第二轮自动换成零输入，期望 33 个零。展示实际
`TT_ROW_REDUCE_RESULT` 的 actual/reference 与 `quantized_bits_hex` 输入位模式。只改 `cols=32`，
先预测 tile 数和结果，再运行。独立完成一次 decimals 输入的误差检查，
最后用自己的话解释一次漏 tile 怎样被反例测试发现。

CPU 测试检查 reference 与布局；host 链接、device JIT 和模拟器执行分别
检查后续阶段。保存的结果与复查方式见[验证说明](validation.md)。模拟器
耗时、timer 和 cycle counter 不能表示真实卡速度。
