import hashlib
import os
import tempfile
import unittest

import LeagueArenaTools as app


class FakeResponse:
    def __init__(self, content, url="https://release-assets.githubusercontent.com/file"):
        self.content = content
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield self.content[:3]
        yield self.content[3:]


class FakeSession:
    def __init__(self, content):
        self.content = content

    def get(self, *_args, **_kwargs):
        return FakeResponse(self.content)


def release(version, content=b"update"):
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [{
            "name": app.UPDATE_ASSET_NAME,
            "browser_download_url": (
                "https://github.com/Naiban/LeagueArenaTools/releases/download/"
                f"v{version}/LeagueArenaTools.exe"
            ),
            "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "size": len(content),
        }],
    }


class UpdaterTests(unittest.TestCase):
    def test_newer_release_is_selected(self):
        self.assertEqual(app._release_update(release("1.02"), "1.01")["version"], "1.02")

    def test_equal_and_older_releases_are_ignored(self):
        self.assertIsNone(app._release_update(release("1.01"), "1.01"))
        self.assertIsNone(app._release_update(release("1.0"), "1.01"))

    def test_missing_digest_is_rejected(self):
        metadata = release("1.02")
        metadata["assets"][0]["digest"] = None
        with self.assertRaises(app.UpdateError):
            app._release_update(metadata, "1.01")

    def test_non_github_asset_url_is_rejected(self):
        metadata = release("1.02")
        metadata["assets"][0]["browser_download_url"] = "https://example.com/update.exe"
        with self.assertRaises(app.UpdateError):
            app._release_update(metadata, "1.01")

    def test_download_is_verified(self):
        content = b"verified update fixture"
        update = app._release_update(release("1.02", content), "1.01")
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "update.exe")
            app._download_update(update, destination, FakeSession(content))
            with open(destination, "rb") as downloaded:
                self.assertEqual(downloaded.read(), content)

    def test_bad_download_is_deleted(self):
        update = app._release_update(release("1.02", b"expected"), "1.01")
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "update.exe")
            with self.assertRaises(app.UpdateError):
                app._download_update(update, destination, FakeSession(b"tampered"))
            self.assertFalse(os.path.exists(destination))


if __name__ == "__main__":
    unittest.main()
