"""骨签 hook-15-日切闭窗硬闸。

本空桩已挂硬实现：开关、物化指纹封签、flock 同锁与中文旁路均在
``app.services.日切闭窗``；投递联拒在 ``app.services.投递``，
互证同票在 ``app.services.互证``。三红分属三服务救绿，非单点校验。
工程形态：物化文件封住闭窗开关指纹，并拒闭窗后回刷投递。
"""

NAME = "日切闭窗硬闸"
HOOK = "hook-15-日切闭窗硬闸"
WIRED = True  # 已挂硬实现，/api/空桩 不得再列为空桩
已挂硬实现 = True
WIRED_SERVICE = "app.services.日切闭窗"
ENGINEERING_FORM = "物化指纹封签 + 拒闭窗后回刷（独立闭窗旁路集合）"
