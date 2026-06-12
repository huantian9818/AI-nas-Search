class QnapError(Exception):
    user_message = "NAS 请求失败"

    def __str__(self) -> str:
        return self.user_message


class QnapConnectionError(QnapError):
    user_message = "无法连接 NAS，请检查地址、端口和网络"


class QnapAuthenticationError(QnapError):
    user_message = "NAS 用户名或密码错误"


class QnapTwoStepRequired(QnapError):
    user_message = "此账号启用了两步验证，请改用未启用两步验证的只读账号"


class QnapPermissionError(QnapError):
    user_message = "NAS 账号没有读取该目录的权限"


class QnapProtocolError(QnapError):
    user_message = "NAS 返回了无法识别的数据"
