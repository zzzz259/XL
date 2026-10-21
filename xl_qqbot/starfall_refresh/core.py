import base64
import copy
import hashlib
import hmac
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SDK_URL = "https://p4cn.17995api.net/api/auth"
LOGIN_URL = "https://elpis-accverify.haoplay.com.cn/v1/account/login"


class SafeError(ValueError):
    pass


def claims(token):
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, IndexError, TypeError):
        raise SafeError("INVALID_TOKEN") from None


def numeric(value):
    return type(value) is int


def validate(data):
    try:
        c = data["credential"]
        p = data["profile"]
        q = p["params"]
        d = claims(c["token"])
        if data["version"] != 1 or any(
            not isinstance(p[k], str) or not p[k] for k in ("appkey", "signing_input")
        ):
            raise ValueError()
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in q.items()):
            raise ValueError()
        if (
            str(d["uid"]) != c["open_id"]
            or str(d["appid"]) != q["appid"]
            or c["channel"] != q["channel"]
        ):
            raise ValueError()
        if not c["channel"] or not d.get("provider"):
            raise ValueError()
        if not all(numeric(d.get(k)) for k in ("iat", "vfp", "exp")):
            raise ValueError()
        if not d["iat"] <= d["vfp"] < d["exp"]:
            raise ValueError()
        return d
    except (KeyError, TypeError, ValueError, SafeError):
        raise SafeError("INVALID_PROFILE_OR_BINDING") from None


def form(params, appkey, signing_input, epoch):
    from urllib.parse import quote

    enc = lambda x: quote(str(x), safe="~()*!.'-_")
    pairs = [
        enc(k) + "=" + enc(v)
        for k, v in params.items()
        if k not in ("appkey", "config") and not k.lower().startswith("private")
    ]
    pairs += ["time=" + str(int(epoch)), "sys=android", "tz=-480"]
    raw = "&".join(pairs)
    md5 = lambda x: hashlib.md5(x.encode()).hexdigest()
    key = md5(md5(appkey) + signing_input)
    sig = base64.b64encode(
        hmac.new(key.encode(), raw.encode(), hashlib.sha1).digest()
    ).decode()
    return (raw + "&sig=" + enc(sig)).encode()


class Store:
    """Private directory + OS lock + atomic replacement. File is NOT encrypted."""

    def __init__(self, path):
        self.path = Path(path).absolute()

    def _check_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise SafeError("SYMLINK_NOT_ALLOWED")
        if os.name != "nt":
            for p in (
                (self.path.parent, self.path)
                if self.path.exists()
                else (self.path.parent,)
            ):
                st = p.stat()
                if st.st_uid != os.getuid() or st.st_mode & 0o077:
                    raise SafeError("PRIVATE_PERMISSIONS_REQUIRED")

    @contextmanager
    def locked(self):
        self._check_directory()
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        if lock.is_symlink():
            raise SafeError("SYMLINK_NOT_ALLOWED")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, 0)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise SafeError("BUSY") from None
            else:
                import fcntl

                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise SafeError("BUSY") from None
            yield
        finally:
            os.close(fd)

    def load(self):
        self._check_directory()
        try:
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                raise ValueError()
            data = json.loads(raw)
            validate(data)
            return data
        except (OSError, ValueError, TypeError):
            raise SafeError("STATE_READ_FAILED") from None

    def save(self, data):
        self._check_directory()
        validate(data)
        fd, name = tempfile.mkstemp(prefix=".refresh-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf8") as stream:
                json.dump(data, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def initialize(self, data):
        with self.locked():
            if self.path.exists():
                raise SafeError("ALREADY_CONFIGURED")
            data = copy.deepcopy(data)
            validate(data)
            data.update(
                instance=uuid.uuid4().hex,
                sequence=0,
                pending=[],
                last_state=None,
                last_attempt=None,
            )
            self.save(data)


def classify(response):
    if not isinstance(response, dict):
        return {"state": "UNKNOWN"}
    code = response.get("retCode")
    if not numeric(code):
        return {"state": "UNKNOWN"}
    if code != 0:
        return {"state": "AUTH_REJECTED"}
    data = response.get("data")
    if not isinstance(data, dict):
        return {"state": "UNKNOWN"}
    value = data.get("maintainState")
    if type(value) in (int, float) and value == 1:
        return {"state": "MAINTENANCE"}
    return {"state": "LOGIN_GATE_PASSED"}


class Engine:
    def __init__(self, store, http, now=time.time, cooldown=900, track_events=True):
        self.store = store
        self.http = http
        self.now = now
        self.cooldown = max(900, cooldown)
        self.track_events = track_events

    def _login(self, c):
        data = {
            "openId": c["open_id"],
            "token": c["token"],
            "channel": c["channel"],
            "platform": 1,
            "buildVersion": "",
            "authRemote": True,
        }
        return self.http(
            LOGIN_URL, json.dumps(data).encode(), {"Content-Type": "application/json"}
        )

    def tick(self, gateway=False):
        with self.store.locked():
            data = self.store.load()
            old = validate(data)
            now = int(self.now())
            refresh = "NOT_DUE"
            prefetched = None
            if now >= old["exp"]:
                refresh = "AUTH_ACTION_REQUIRED"
                status = {"state": "AUTH_REQUIRED"}
            else:
                if now >= old["vfp"] + 60:
                    last = data.get("last_attempt")
                    if numeric(last) and now - last < self.cooldown:
                        refresh = "COOLDOWN"
                    else:
                        # Persist the attempt before networking, including crash/restart cases.
                        data["last_attempt"] = now
                        self.store.save(data)
                        try:
                            p = data["profile"]
                            res = self.http(
                                SDK_URL,
                                form(p["params"], p["appkey"], p["signing_input"], now),
                                {
                                    "Authorization": data["credential"]["token"],
                                    "Content-Type": "application/x-www-form-urlencoded",
                                    "Accept": "application/json",
                                },
                            )
                            refresh = "CANDIDATE_REJECTED"
                            if isinstance(res, dict) and res.get("code") == 401:
                                refresh = "AUTH_ACTION_REQUIRED"
                            elif (
                                isinstance(res, dict)
                                and numeric(res.get("code"))
                                and res["code"] == 0
                                and isinstance(res.get("jwt"), str)
                            ):
                                try:
                                    new = claims(res["jwt"])
                                except SafeError:
                                    new = {}
                                valid = (
                                    all(
                                        new.get(k) == old.get(k)
                                        for k in ("uid", "appid", "provider")
                                    )
                                    and all(
                                        numeric(new.get(k))
                                        for k in ("iat", "vfp", "exp")
                                    )
                                    and old["iat"] < new["iat"] <= now + 300
                                    and now < new["vfp"] < new["exp"]
                                )
                                if valid:
                                    candidate = dict(
                                        data["credential"], token=res["jwt"]
                                    )
                                    checked = self._login(candidate)
                                    if classify(checked)["state"] in (
                                        "MAINTENANCE",
                                        "LOGIN_GATE_PASSED",
                                    ):
                                        data["credential"] = candidate
                                        self.store.save(data)
                                        if (
                                            self.store.load()["credential"]["token"]
                                            != candidate["token"]
                                        ):
                                            raise SafeError("READBACK_FAILED")
                                        refresh = "UPDATED"
                                        prefetched = checked
                                    else:
                                        refresh = "CANDIDATE_NOT_ACCEPTED"
                        except Exception:
                            refresh = "REFRESH_ERROR"
                try:
                    response = (
                        prefetched
                        if prefetched is not None
                        else self._login(data["credential"])
                    )
                    status = classify(response)
                    if gateway and status["state"] == "LOGIN_GATE_PASSED":
                        addresses = response.get("data", {}).get("addresses", [])
                        reach = self.http.gateway(addresses)
                        status["gateway"] = reach
                        status["state"] = (
                            "ONLINE" if reach == "REACHABLE" else "GATEWAY_UNKNOWN"
                        )
                except Exception:
                    status = {"state": "UNKNOWN"}
            if refresh == "AUTH_ACTION_REQUIRED" and status["state"] != "AUTH_REQUIRED":
                status = dict(
                    status, service_state=status["state"], state="AUTH_ACTION_REQUIRED"
                )
            state = status["state"]
            if self.track_events and data.get("last_state") != state:
                if data.get("last_state") is not None:
                    data["sequence"] = data.get("sequence", 0) + 1
                    data.setdefault("pending", []).append(
                        {
                            "key": f"starfall:{data['instance']}:{data['sequence']}",
                            "previous": data["last_state"],
                            "state": state,
                            "observed_at": now,
                        }
                    )
                    data["pending"] = data["pending"][-100:]
                data["last_state"] = state
            self.store.save(data)
            c = claims(data["credential"]["token"])
            return dict(
                status,
                refresh=refresh,
                observed_at=now,
                iat=c["iat"],
                vfp=c["vfp"],
                exp=c["exp"],
                event=data.get("pending", [None])[0] if data.get("pending") else None,
            )

    def ack(self, key):
        with self.store.locked():
            d = self.store.load()
            d["pending"] = [x for x in d.get("pending", []) if x["key"] != key]
            self.store.save(d)
