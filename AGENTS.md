使用 lang chain 以及 lang graph 进行agent开发
使用 uv 进行 python 环境创建，使用 python3.10 ，所有使用的python包通过 requirements.txt 进行维护与管理。
提示词、规则、密钥等信息全部外置，放到 .env 或者 .yaml/.md 中

要对代码功能拆分，一个 python 文件有自己的定位以及功能，不要一个文件写太多东西。

较为复杂的逻辑以及较长的函数要有合理的注释。每个大类以及其成员需要一个简短明确的注释来说明其作用

使用sqlite作为数据持久化的数据库