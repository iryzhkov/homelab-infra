"""Shared closed Authentik API and private-file custody helpers."""
import datetime as dt
import json
import os
import stat
import uuid
import urllib.parse
import urllib.request

ORIGIN = "https://auth.ryzhkov.dev"
API = ORIGIN + "/api/v3"
PATHS = {
    "user": "/core/users/", "mapping": "/propertymappings/provider/scope/",
    "provider": "/providers/oauth2/",
    "policy": "/policies/expression/",
    "application": "/core/applications/", "binding": "/policies/bindings/",
    "token": "/core/tokens/",
}

class Refused(Exception):
    pass


def require(ok):
    if not ok:
        raise Refused()


def stable(value):
    if isinstance(value, list):
        return sorted((stable(x) for x in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()}
    return value


def matches(seen, body):
    from ansible.module_utils.cohort_signer import body as approved_body
    body = approved_body(seen.get("pk"), body)
    for k, v in body.items():
        got = seen.get(k)
        if k == "expires":
            try:
                if dt.datetime.fromisoformat(got.replace("Z", "+00:00")) != dt.datetime.fromisoformat(v.replace("Z", "+00:00")):
                    return False
            except Exception:
                return False
        elif stable(got) != stable(v):
            return False
    return True


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"))
    return dt.datetime.fromisoformat(value[:-1] + "+00:00")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refused()


class APIClient:
    def __init__(self, token, paths=None):
        from ansible.module_utils.cohort_signer import generation
        generation()  # Require the common lock when rotation is enrolled.
        self.paths = paths or PATHS
        require(isinstance(token, str) and 16 <= len(token) <= 4096 and "\n" not in token and "\r" not in token)
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, public=False):
        require(isinstance(path, str) and path.startswith("/") and not path.startswith("//"))
        endpoint = ORIGIN + path if public else API + path
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if not public:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=8) as response:
                require(response.status in (200, 201, 204))
                raw = response.read(2 * 1024 * 1024 + 1)
                require(len(raw) <= 2 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except Exception:
            raise Refused() from None

    def require_superuser(self):
        # Both collection and used_by views can silently filter non-admin reads.
        actor = self.request("GET", "/core/users/me/")
        require(isinstance(actor, dict) and isinstance(actor.get("user"), dict)
                and actor["user"].get("is_superuser") is True)

    def mapping_uses(self, pk):
        require(isinstance(pk, str) and str(uuid.UUID(pk)) == pk)
        # Check authority here, independent of any previous application load.
        self.require_superuser()
        result = self.request("GET", "/propertymappings/all/" + pk + "/used_by/")
        require(isinstance(result, list) and len(result) <= 500)
        fields = {"app", "model_name", "pk", "name", "action"}
        require(all(isinstance(row, dict) and set(row) == fields
                    and all(isinstance(value, str) and 1 <= len(value) <= 1024
                            for value in row.values()) for row in result))
        return result

    def rows(self, kind, owned=None):
        # Complete public discovery; never list secret-bearing OAuth2 bodies.
        path = ("/providers/all/" if kind == "provider" else self.paths[kind]) + "?page_size=500"
        if kind == "provider":
            self.require_superuser()
        if kind == "application":
            # A matching pagination count is not completeness evidence when
            # Authentik silently filters applications for a non-superuser.
            self.require_superuser()
            path += "&superuser_full_list=true"
        result = self.request("GET", path)
        require(isinstance(result, dict) and isinstance(result.get("results"), list))
        rows = result["results"]
        require(len(rows) <= 500 and not result.get("pagination", {}).get("next"))
        require(result.get("pagination", {}).get("count") == len(rows))
        if kind == "provider":
            # Only the original receipt ID permits an OAuth2 detail read.
            # Check all public name/ID collisions before opening any such body.
            records = [r for r in (owned or {}).values() if r["kind"] == kind]
            for record in records:
                pk = record["pk"]
                require(type(pk) is int and pk > 0 and record["lookup"] == pk)
                found = [r for r in rows if r.get("pk") == pk]
                named = [r for r in rows if r.get("name") == record["body"]["name"]]
                require(len(found) == len(named) == 1 and found[0] == named[0])
            for record in records:
                detail = self.request("GET", self.paths[kind] + str(record["pk"]) + "/")
                require(isinstance(detail, dict) and detail.get("pk") == record["pk"]
                        and matches(detail, record["body"]))
                rows = [detail if r.get("pk") == record["pk"] else r for r in rows]
        return rows


def secure_dir(path):
    require(isinstance(path, str) and path.startswith("/") and os.path.normpath(path) == path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path[1:].split("/"):
            st = os.fstat(fd)
            require(st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022)
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        st = os.fstat(fd)
        require(st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700)
        return fd
    except Exception:
        os.close(fd)
        raise Refused() from None


def read_file(fd, name, limit=4096):
    try:
        leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(leaf, "rb") as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size <= limit)
        raw = stream.read(limit + 1)
        require(len(raw) <= limit)
        return raw


def read_private(fd, name):
    raw = read_file(fd, name, 1048576)
    if raw is None:
        raise FileNotFoundError(name)
    return json.loads(raw)


def atomic(fd, name, content):
    temp = ".host-" + str(uuid.uuid4())
    leaf = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    with os.fdopen(leaf, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)

def write_private(fd, name, value):
    try:
        read_private(fd, name)
    except FileNotFoundError:
        pass
    atomic(fd, name, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
