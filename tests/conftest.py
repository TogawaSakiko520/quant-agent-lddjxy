"""普通测试在收集业务模块前禁止网络连接；真实授权验证只能通过独立 CLI。"""

import socket
from typing import Any


def prohibit_external_connection(*args: Any, **kwargs: Any) -> Any:
    """阻断真实 socket 连接，不记录地址、请求或密钥；脱敏传输测试仍可运行。"""
    raise AssertionError("普通测试（包括模块导入）禁止外部网络连接")


# conftest在测试模块收集前加载，早于fixture；防止SDK导入或默认CLI暗中查账户。
# 测试进程结束即释放此补丁，不修改用户环境或独立Paper进程。
socket.socket.connect = prohibit_external_connection  # type: ignore[method-assign]
socket.socket.connect_ex = prohibit_external_connection  # type: ignore[method-assign]
socket.create_connection = prohibit_external_connection
