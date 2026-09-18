from app.bootstrap.shell_contribution import ApplicationShellContribution


class _Service:
    def current(self):
        return None


class _Signal:
    def __init__(self):
        self.handlers = []

    def connect(self, handler):
        self.handlers.append(handler)


class _Controller:
    progress_changed = _Signal()

    def __init__(self):
        self.calls = []
        self.service = _Service()
        self.check_state_changed = _Signal()

    def check_update(self, **kwargs):
        self.calls.append(kwargs)


class _Registry:
    def __init__(self, controller):
        self.controller = controller

    def get(self, key):
        assert key == "versions"
        return type("Feature", (), {"controller": self.controller})()


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message):
        self.messages.append(message)


class _Shell:
    def __init__(self):
        self.status_bar = _StatusBar()
        self.scheduled = []

    def schedule(self, delay_ms, callback):
        self.scheduled.append((delay_ms, callback))

    def _on_check_state_changed(self, checking):
        self.checking = checking

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _InstallRegistry:
    def __init__(self, versions):
        self.features = {
            "versions": type("Feature", (), {"controller": versions})(),
            "preview": type("Feature", (), {"controller": _FeatureController(), "page": _FeaturePage()})(),
            "audio": type("Feature", (), {"controller": _FeatureController(), "page": _FeaturePage()})(),
            "character": type("Feature", (), {"controller": _FeatureController(), "page": _FeaturePage()})(),
            "importer": type("Feature", (), {"controller": _ImporterController()})(),
        }

    def get(self, key):
        return self.features[key]


class _FeatureController:
    progress_changed = _Signal()

    def restore_local(self):
        pass


class _FeaturePage:
    close_requested = _Signal()


class _ImporterController:
    progress_stage = _Signal()
    category_progress = _Signal()
    stage_finished = _Signal()
    category_finished = _Signal()
    all_finished = _Signal()


def test_first_start_update_check_is_silent(monkeypatch):
    controller = _Controller()
    contribution = ApplicationShellContribution(_Registry(controller), None, None)
    shell = _Shell()
    contribution.shell = shell

    contribution.schedule_update_check()

    assert shell.status_bar.messages == ["首次启动, 自动检查更新..."]
    assert len(shell.scheduled) == 1
    delay_ms, callback = shell.scheduled[0]
    assert delay_ms == 1500
    callback()
    assert controller.calls == [{"notify_errors": False}]


def test_install_routes_check_state_to_shell():
    controller = _Controller()
    contribution = ApplicationShellContribution(_InstallRegistry(controller), None, None)
    shell = _Shell()

    contribution.install(shell)

    assert controller.check_state_changed.handlers == [shell._on_check_state_changed]
