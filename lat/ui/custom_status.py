"""Custom Status page: League status text editor."""

import threading


class CustomStatusMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _defer_custom_status_edit(self, _event=None):
        self.root.after_idle(self._sync_custom_status_draft)

    def _on_custom_status_edit(self, _event=None):
        self._sync_custom_status_draft()

    def _sync_custom_status_draft(self):
        if not hasattr(self, "custom_status_editor"):
            return ""
        text = self.custom_status_editor.get("1.0", "end-1c")
        self.live_config["custom_status_text"] = text
        count = len(text)
        self.custom_status_count_var.set(f"{count} character{'s' if count != 1 else ''}")
        self._schedule_save()
        return text

    def _set_custom_status_editor(self, text):
        self.custom_status_editor.delete("1.0", "end")
        self.custom_status_editor.insert("1.0", text)
        self._sync_custom_status_draft()

    def _load_custom_status(self):
        self._start_custom_status_request(None)

    def _apply_custom_status(self):
        self._start_custom_status_request(self._sync_custom_status_draft())

    def _clear_custom_status(self):
        self._set_custom_status_editor("")
        self._start_custom_status_request("")

    def _start_custom_status_request(self, new_status):
        if self._status_request_running:
            self.custom_status_info_var.set("Request already running")
            return
        if not self.client.is_connected or self._connection_state is not True:
            self.custom_status_info_var.set("League client is offline")
            self.log("Custom status: League client is not connected.")
            return
        self._status_request_running = True
        self.custom_status_info_var.set("Loading..." if new_status is None else "Saving...")
        threading.Thread(
            target=self._custom_status_worker,
            args=(new_status,),
            name="LeagueArenaTools-CustomStatus",
            daemon=True,
        ).start()

    def _custom_status_worker(self, new_status):
        try:
            if new_status is not None:
                response = self.client.put("/lol-chat/v1/me", json={"statusMessage": new_status})
                if response.status_code not in (200, 201, 204):
                    self._finish_custom_status_request(
                        f"Save failed (HTTP {response.status_code})",
                        log_message=f"Custom status save failed (HTTP {response.status_code}).",
                    )
                    return

            response = self.client.get("/lol-chat/v1/me")
            if response.status_code != 200:
                self._finish_custom_status_request(
                    f"Load failed (HTTP {response.status_code})",
                    log_message=f"Custom status load failed (HTTP {response.status_code}).",
                )
                return
            data = response.json()
            stored_status = data.get("statusMessage", "") if isinstance(data, dict) else ""
            if not isinstance(stored_status, str):
                stored_status = str(stored_status or "")

            if new_status is None:
                message = f"Loaded {len(stored_status)} characters"
                log_message = f"Loaded League custom status ({len(stored_status)} characters)."
            elif stored_status == new_status:
                message = f"Saved {len(stored_status)} characters"
                log_message = f"League custom status saved ({len(stored_status)} characters)."
            else:
                message = f"League stored {len(stored_status)} of {len(new_status)} characters"
                log_message = (
                    "League altered or truncated the custom status: "
                    f"stored {len(stored_status)} of {len(new_status)} characters."
                )
            self._finish_custom_status_request(message, stored_status, log_message)
        except Exception as e:
            self._finish_custom_status_request(
                "Request failed", log_message=f"Custom status error: {e}"
            )

    def _finish_custom_status_request(self, message, stored_status=None, log_message=None):
        def finish():
            if self._closing:
                return
            self._status_request_running = False
            self.custom_status_info_var.set(message)
            if stored_status is not None:
                self._set_custom_status_editor(stored_status)
            if log_message:
                self.log(log_message)
        self._run_on_ui(finish)
