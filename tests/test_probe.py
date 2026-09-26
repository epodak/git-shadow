import unittest


from git_shadow.probe import RemoteProbe


class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _Engine:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    def _run_ssh(self, remote_cmd, capture=True, check=False):
        if self.exc:
            raise self.exc
        return self.result


class TestRemoteCapabilityProbe(unittest.TestCase):
    def test_cloudcli_capability_ready(self):
        engine = _Engine(_Result(
            stdout=(
                "installed=1\n"
                "running=1\n"
                "reachable=1\n"
                "verified=1\n"
                "available=1\n"
                "pid=1234\n"
                "port=3001\n"
                "reason=ready\n"
            )
        ))
        capability = RemoteProbe("aws").probe_cloudcli(engine)
        self.assertTrue(capability["available"])
        self.assertTrue(capability["reachable"])
        self.assertTrue(capability["verified"])
        self.assertEqual(capability["pid"], "1234")
        self.assertEqual(capability["port"], "3001")
        self.assertEqual(capability["reason"], "ready")

    def test_cloudcli_connection_refused_is_unavailable(self):
        engine = _Engine(_Result(
            stdout=(
                "installed=1\n"
                "running=1\n"
                "reachable=0\n"
                "verified=1\n"
                "available=0\n"
                "pid=1234\n"
                "port=3001\n"
                "reason=cloudcli-connection-refused\n"
            )
        ))
        capability = RemoteProbe("aws").probe_cloudcli(engine)
        self.assertFalse(capability["available"])
        self.assertTrue(capability["running"])
        self.assertFalse(capability["reachable"])
        self.assertEqual(capability["reason"], "cloudcli-connection-refused")

    def test_cloudcli_probe_ssh_failure_degrades_cleanly(self):
        capability = RemoteProbe("broken").probe_cloudcli(
            _Engine(exc=RuntimeError("ssh down"))
        )
        self.assertFalse(capability["available"])
        self.assertFalse(capability["running"])
        self.assertIn("ssh-probe-failed", capability["reason"])


if __name__ == "__main__":
    unittest.main()
