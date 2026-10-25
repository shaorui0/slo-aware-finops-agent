
## archive/intermediate/

第一轮重录（按 `namespace,workload,container` 顺序的选择器）产生的盒带，从未被提交的代码引用：生成的样例夹具里一个盒带的 12 位短键恰好是 12 个数字，被仓库的泄漏扫描当成账号号误报。把选择器改成 kube-state 的标签顺序（`container,namespace,workload`）后提示词变了，整批重录。这些盒带原样保留，没有删除。

## archive/intermediate_r2/

第二轮重录产生的 36 个内存决策盒带：当时趋势证据对每条内存决策都加了两条；最终版本只在趋势触发时加证据（与批任务的 job 行同理），这些提示词不再出现。同样没有被提交的代码引用，原样保留。
