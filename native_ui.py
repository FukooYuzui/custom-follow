"""Non-blocking WinForms dispatch for lifecycle operations.

Never wait for JavaScript or a GUI-thread callback from a closing handler.
The application uses Windows WebView2; all delegates below are posted rather
than synchronously invoked, including acknowledgements from its pinned bridge.
"""
from __future__ import annotations

import logging


def post_window(window, action) -> bool:
    if window is None:
        return False
    try:
        form = window.native
        if form is None or form.IsDisposed:
            return False
        from System import Action

        def invoke():
            try:
                if not form.IsDisposed:
                    action(form)
            except Exception as exc:
                # Closing/disposed windows are expected during a shutdown race.
                logging.debug('Deferred UI operation skipped (%s)', type(exc).__name__)

        form.BeginInvoke(Action(invoke))
        return True
    except Exception as exc:
        logging.debug('Window dispatch unavailable (%s)', type(exc).__name__)
        return False


def post_js(window, script: str) -> bool:
    def execute(form):
        core = form.browser.webview.CoreWebView2
        if core is not None:
            core.ExecuteScriptAsync(script)
    return post_window(window, execute)


def close_window(window) -> bool:
    return post_window(window, lambda form: form.Close())


def show_window(window) -> bool:
    def show(form):
        from System.Windows.Forms import FormWindowState
        if form.WindowState == FormWindowState.Minimized:
            form.WindowState = FormWindowState.Normal
        form.Show()
        form.Activate()
    return post_window(window, show)


def install_bridge_delivery(window) -> None:
    """Don't block a bridge thread delivering a return value during shutdown.

    pywebview 6.2 routes its RPC acknowledgements through synchronous evaluate_js,
    even though it ignores the result. Dispatch those acknowledgements without
    waiting; expressions whose Python result is actually needed keep the SDK's
    normal implementation. A closed renderer can no longer strand an RPC thread.
    """
    if getattr(window, '_custom_follow_bridge_delivery', False):
        return
    original = window.evaluate_js

    def evaluate(script, callback=None):
        if callback is None and isinstance(script, str) and script.startswith('window.pywebview._returnValuesCallbacks['):
            post_js(window, script)
            return None
        return original(script, callback)

    window.evaluate_js = evaluate
    window._custom_follow_bridge_delivery = True
