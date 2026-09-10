# NEU_harness_hackathon

目前的初步想法是让比较强的模型（GPT, Claude)生成特定领域的合成数据,再由我们开发harness/agent工具帮助对应的推理模型（Qwen，Gemini flash)来处理这些数据中的问题。合成数据中需要刻意设置问题例如变量不匹配，数据缺失等等。
我们通过开发harness/agent来优化这些过程。

1.大家可以挑自己感兴趣的领域生成对应的数据例如医疗，保险，软件开发，教育等等；
2.目前推理模型的实现待定。是尝试本地运行小模型如qwen还是接api运行Gemini flash；
