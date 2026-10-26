"""Update checks and installation from the UI."""

import os
import sys
import subprocess
import threading
from tkinter import messagebox

from lat.constants import APP_TITLE, __version__
from lat.http import _http
from lat.updater import (
    build_update_script, _download_update, GITHUB_LATEST_RELEASE_API, _release_update,
    update_failed_marker_path, UpdateError,
)


class UpdatesMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _on_auto_update_toggle(self):
        if self.var_auto_update.get() and not self._available_update:
            self._check_for_updates(manual=True)

    def _check_for_updates(self, manual=False):
        if self._closing or self._update_check_running or self._update_installing:
            return
        self._update_check_running = True
        self.update_status_var.set("Checking GitHub Releases...")
        self.update_check_button.configure(state="disabled", cursor="arrow")

        def worker():
            try:
                response = _http().get(
                    GITHUB_LATEST_RELEASE_API,
                    timeout=(5, 15),
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": APP_TITLE,
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                if response.status_code == 404:
                    raise UpdateError(
                        "Update service unavailable while the GitHub repository is private."
                    )
                response.raise_for_status()
                update = _release_update(response.json())
                self._run_on_ui(lambda: self._finish_update_check(update, None, manual))
            except UpdateError as exc:
                self._run_on_ui(lambda error=str(exc): self._finish_update_check(None, error, manual))
            except (_http().RequestException, ValueError):
                self._run_on_ui(lambda: self._finish_update_check(
                    None, "Could not reach GitHub Releases.", manual))

        threading.Thread(target=worker, name="LeagueArenaTools-UpdateCheck", daemon=True).start()

    def _finish_update_check(self, update, error, manual):
        if self._closing:
            return
        self._update_check_running = False
        self.update_check_button.configure(state="normal", cursor="hand2")
        if error:
            self.update_status_var.set(error)
            if manual:
                self.log(f"Update check: {error}")
            return
        self._available_update = update
        if update:
            self.update_status_var.set(f"Version {update['version']} is available.")
            self.update_install_button.configure(state="normal", cursor="hand2")
            self.log(f"LeagueArenaTools v{update['version']} is available.")
        else:
            self.update_status_var.set(f"LeagueArenaTools v{__version__} is up to date.")
            self.update_install_button.configure(state="disabled", cursor="arrow")
            if manual:
                self.log("No newer LeagueArenaTools release is available.")

    def _install_available_update(self):
        update = self._available_update
        if not update or self._update_installing:
            return
        if not getattr(sys, "frozen", False):
            message = "Automatic installation is available only in LeagueArenaTools.exe."
            self.update_status_var.set(message)
            self.log(message)
            return
        if not messagebox.askyesno(
            "Install update",
            f"Download and install LeagueArenaTools v{update['version']} now?\n\n"
            "The application will close and restart automatically.",
            parent=self.root,
        ):
            return
        self._update_installing = True
        self.update_status_var.set(f"Downloading version {update['version']}...")
        self.update_check_button.configure(state="disabled", cursor="arrow")
        self.update_install_button.configure(state="disabled", cursor="arrow")
        destination = os.path.join(
            os.path.dirname(sys.executable), f".{APP_TITLE}-update-{os.getpid()}.exe"
        )
        try:
            os.remove(destination)
        except OSError:
            pass

        def worker():
            try:
                _download_update(update, destination)
                self._run_on_ui(lambda: self._apply_downloaded_update(destination, update))
            except (UpdateError, _http().RequestException, OSError) as exc:
                self._run_on_ui(lambda error=str(exc): self._update_install_failed(error))

        threading.Thread(target=worker, name="LeagueArenaTools-UpdateDownload", daemon=True).start()

    def _update_install_failed(self, error):
        if self._closing:
            return
        self._update_installing = False
        message = error or "The update could not be downloaded."
        self.update_status_var.set(message)
        self.update_check_button.configure(state="normal", cursor="hand2")
        self.update_install_button.configure(state="normal", cursor="hand2")
        self.log(f"Update installation failed: {message}")

    def _apply_downloaded_update(self, downloaded_path, update):
        if self._closing:
            return
        executable = os.path.abspath(sys.executable)
        script_path = os.path.join(
            os.path.dirname(executable), f".{APP_TITLE}-update-{os.getpid()}.cmd"
        )
        script = build_update_script(os.getpid(), executable, downloaded_path,
                                     update_failed_marker_path(), update["version"])
        try:
            with open(script_path, "w", encoding="ascii", newline="") as output:
                output.write(script)
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                ["cmd.exe", "/c", script_path], cwd=os.path.dirname(executable),
                creationflags=creation_flags, close_fds=True,
            )
        except OSError as exc:
            try:
                os.remove(downloaded_path)
            except OSError:
                pass
            self._update_install_failed(str(exc))
            return
        self.update_status_var.set(f"Installing version {update['version']}...")
        self.log(f"Installing LeagueArenaTools v{update['version']} and restarting.")
        self.root.after(150, self._final_close)
