# template/middleware.py（和 settings.py 同级目录）
class DummyUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 给 request 加一个 dummy user，仅满足 rosetta 的属性检查
        class DummyUser:
            is_authenticated = True  # 模拟已登录
            is_staff = True  # 模拟 staff 权限（rosetta 要求）
            is_superuser = True  # 模拟超级用户权限

        request.user = DummyUser()  # 给 request 绑定 user 属性
        response = self.get_response(request)
        return response