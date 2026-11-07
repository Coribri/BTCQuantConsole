"""Protocol v1 adapter example. Run: python examples/external_model_server.py

This is a connectivity example, not a trained or validated strategy. Replace
predict() with your model inference, preserving its versioned JSON response.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


def predict(request):
    indicators = request.get("indicators", {})
    side = "LONG" if indicators.get("entry_signal") else "SHORT" if indicators.get("short_entry_signal") else "WAIT"
    return {"protocol":"btcquant.model.v1","model_id":request["model_id"],
            "bar_time":request["bar_time"],"direction":side,"exit_long":False,"exit_short":False,
            "reason":"HTTP 适配器示例：透传预热后的趋势条件，尚未经收益验证"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path!="/predict":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length",0))
        if not 0<length<=1_000_000:
            self.send_error(413)
            return
        try:
            data=predict(json.loads(self.rfile.read(length)))
            raw=json.dumps(data,ensure_ascii=False).encode()
        except (ValueError,KeyError,TypeError):
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self,*args):
        pass


if __name__=="__main__":
    print("Model adapter listening on http://127.0.0.1:9001/predict")
    ThreadingHTTPServer(("127.0.0.1",9001),Handler).serve_forever()
