class QnapError(Exception):
    user_message = "NAS 请求失败"

    def __str__(self) -> str:
        return self.user_message


class QnapConnectionError(QnapError):
    user_message = "无法连接 NAS，请检查地址、端口和网络"


class QnapTlsVerificationError(QnapConnectionError):
    user_message = (
        "HTTPS 证书校验失败，请改用证书匹配的域名，"
        "或开启忽略 HTTPS 证书校验"
    )


class QnapAuthenticationError(QnapError):
    user_message = "NAS 用户名或密码错误"


class QnapSessionExpired(QnapAuthenticationError):
    user_message = "NAS 登录已过期，请重新登录"


class QnapTwoStepRequired(QnapError):
    user_message = "此账号启用了两步验证，请改用未启用两步验证的只读账号"


class QnapPermissionError(QnapError):
    user_message = "NAS 账号没有读取该目录的权限"


class QnapProtocolError(QnapError):
    user_message = "NAS 返回了无法识别的数据"

    def __init__(self, status: object | None = None):
        super().__init__()
        self.status = status

    def __str__(self) -> str:
        if self.status is None:
            return self.user_message
        return f"NAS 返回了未识别的状态码 {self.status}"
