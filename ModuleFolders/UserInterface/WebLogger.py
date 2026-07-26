import os
import re
import sys
import time

from rich.console import Console
from Tools.MCPServer.security import INTERNAL_AUTH_ENV, INTERNAL_AUTH_HEADER


class WebLogger:
    def __init__(self, stream=None, show_detailed=False):
        self.last_stats_time = 0
        self.stream = stream or sys.__stdout__
        self.show_detailed = show_detailed
        self.current_source = ""
        self._last_result_time = 0

    def _push_to_web(self, source, translation):
        internal_api_url = os.environ.get("AINIEE_INTERNAL_API_URL")
        if not internal_api_url:
            return
        try:
            import httpx

            httpx.post(
                f"{internal_api_url}/api/internal/update_comparison",
                json={"source": source, "translation": translation},
                headers={INTERNAL_AUTH_HEADER: os.environ.get(INTERNAL_AUTH_ENV, "")},
                timeout=1.0,
            )
        except Exception:
            pass

    def log(self, msg):
        if not isinstance(msg, str):
            from io import StringIO

            with StringIO() as buf:
                temp_console = Console(file=buf, force_terminal=True, width=120)
                temp_console.print(msg)
                msg_str = buf.getvalue()
        else:
            msg_str = msg

        if "<<<RAW_RESULT>>>" in msg_str:
            if time.time() - self._last_result_time < 0.5:
                return
            try:
                data = msg_str.split("<<<RAW_RESULT>>>")[1].strip()
                if data:
                    self._push_to_web(self.current_source, data)
            except Exception:
                pass
            return

        if msg_str:
            clean = re.sub(r"\[/?[a-zA-Z\s]+\]", "", msg_str)
            try:
                self.stream.write(clean.strip() + "\n")
                self.stream.flush()
            except Exception:
                pass

    def on_source_data(self, event, data):
        if not isinstance(data, dict):
            return
        self.current_source = str(data.get("data", ""))

    def on_result_data(self, event, data):
        if not isinstance(data, dict):
            return

        raw_content = str(data.get("data", ""))
        source_content = data.get("source")
        if not raw_content and not source_content:
            return

        if source_content:
            self.current_source = str(source_content)

        if raw_content:
            self._push_to_web(self.current_source, raw_content)
            self._last_result_time = time.time()

    def update_progress(self, event, data):
        if not data or not isinstance(data, dict):
            return

        if time.time() - self.last_stats_time < 0.5:
            return
        self.last_stats_time = time.time()

        completed = data.get("line", 0)
        total = data.get("total_line", 1)
        tokens = data.get("token", 0)
        elapsed = data.get("time", 0)

        total_req = data.get("total_requests", 0)
        success_req = data.get("success_requests", 0)
        error_req = data.get("error_requests", 0)

        success_rate = (success_req / total_req * 100) if total_req > 0 else 0
        error_rate = (error_req / total_req * 100) if total_req > 0 else 0

        calc_tokens = data.get("session_token", tokens)
        calc_requests = data.get("session_requests", total_req)

        rpm = (calc_requests / (elapsed / 60)) if elapsed > 0 else 0
        tpm_k = (calc_tokens / (elapsed / 60) / 1000) if elapsed > 0 else 0

        try:
            self.stream.write(
                "[STATS] "
                f"RPM: {rpm:.2f} | TPM: {tpm_k:.2f}k | Progress: {completed}/{total} | "
                f"Tokens: {tokens} | S-Rate: {success_rate:.1f}% | E-Rate: {error_rate:.1f}%\n"
            )
            self.stream.flush()
        except Exception:
            pass

    def update_status(self, event, data):
        pass
