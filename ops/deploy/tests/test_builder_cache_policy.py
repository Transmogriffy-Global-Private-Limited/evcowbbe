"""Regression tests for sandbox-compatible offline Go build cache."""

import os
import stat
import tempfile
import unittest
from pathlib import Path


class BuilderCachePolicyTests(unittest.TestCase):
    def test_cache_is_confined_to_approved_build_workspace(self):
        script = Path(
            "ops/deploy/vps/bin/evcowbbe-build-sha"
        ).read_text(encoding="utf-8")

        self.assertIn("go_cache=$work/.go-build-cache", script)
        self.assertIn('export GOCACHE="$go_cache"', script)
        self.assertIn('[[ ! -L $go_cache ]]', script)
        self.assertIn('stat -c %u "$go_cache"', script)
        self.assertIn('stat -c %a "$go_cache"', script)
        self.assertIn('[[ $cache_mode == 700 || $cache_mode == 2700 ]]', script)

        self.assertLess(
            script.index('export GOCACHE="$go_cache"'),
            script.index("go mod verify"),
        )

        for invariant in (
            "GOTOOLCHAIN=local",
            "GOPROXY=off",
            "GOSUMDB=off",
            "GOWORK=off",
            "GOFLAGS=-mod=readonly",
        ):
            self.assertIn(invariant, script)

    def test_setgid_workspace_cache_remains_private(self):
        if os.name != "posix":
            self.skipTest("Requires POSIX directory permissions")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "build-work"
            workspace.mkdir()
            workspace.chmod(0o2750)

            old_umask = os.umask(0o077)
            try:
                cache = workspace / ".go-build-cache"
                cache.mkdir()
            finally:
                os.umask(old_umask)

            mode = stat.S_IMODE(cache.stat().st_mode)

            self.assertIn(mode, (0o700, 0o2700))
            self.assertEqual(mode & 0o077, 0)
    def test_executor_sandbox_remains_strict(self):
        unit = Path(
            "ops/deploy/vps/systemd/evcowbbe-deploy-executor.service"
        ).read_text(encoding="utf-8")

        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=yes", unit)
        self.assertIn("/srv/evcowbbe/build-work", unit)
        self.assertNotIn("/var/lib/evcow-builder", unit)


if __name__ == "__main__":
    unittest.main()
