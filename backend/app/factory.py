from __future__ import annotations
import os
from pathlib import Path
from flask import Flask, jsonify, redirect, request, send_from_directory

from app import seed
from app.services import 会话, 入队, 投递, 死信, 重试, 互证, 端点, 夹具, 说明, 签名目录, 日切闭窗

_STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC), static_url_path="")
    app.secret_key = os.environ.get("密钥", "hook-lab-dev")

    @app.before_request
    def _gate():
        if request.path.startswith("/api/") and request.path not in ("/api/登录",):
            if not 会话.已登录():
                return jsonify({"错误": "未登录"}), 401
        return None

    @app.route("/login")
    def 登录页():
        return send_from_directory(_STATIC, "login.html")

    @app.route("/")
    def 首页():
        return send_from_directory(_STATIC, "index.html")

    @app.route("/<path:name>")
    def 静态页(name):
        p = _STATIC / name
        if p.is_file():
            return send_from_directory(_STATIC, name)
        return redirect("/")

    @app.post("/api/登录")
    def api_登录():
        body = request.get_json(force=True, silent=True) or {}
        账号 = str(body.get("账号") or body.get("user") or "")
        密码 = str(body.get("密码") or body.get("password") or "")
        if 会话.校验登录(账号, 密码):
            会话.写入会话(账号)
            return jsonify({"ok": True, "用户": 账号})
        return jsonify({"ok": False, "错误": "账号或密码不对"}), 401

    @app.post("/api/登出")
    def api_登出():
        会话.登出()
        return jsonify({"ok": True})

    @app.get("/api/概览")
    def api_概览():
        return jsonify(签名目录.概览())

    @app.get("/api/仓情")
    def api_仓情():
        return jsonify(说明.仓情())

    @app.get("/api/签名规")
    def api_签名规_get():
        return jsonify(签名目录.签名规())

    @app.post("/api/签名规")
    def api_签名规_post():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(签名目录.改签名规(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/事件")
    def api_事件():
        return jsonify(签名目录.事件列表())

    @app.get("/api/事件/<eid>")
    def api_事件一条(eid):
        row = 签名目录.事件一条(eid)
        if not row:
            return jsonify({"错误": "无此事件"}), 404
        return jsonify(row)

    @app.post("/api/事件/入队")
    def api_入队():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(入队.建事件(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/投递")
    def api_投递列表():
        return jsonify(签名目录.投递列表(request.args.get("端点键") or None))

    @app.post("/api/投递")
    def api_投递():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(投递.投递一次(body["事件号"], bool(body.get("强制失败"))))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/投递/批量重试")
    def api_批量重试():
        return jsonify(重试.批量重试待投())

    @app.get("/api/死信")
    def api_死信():
        return jsonify(签名目录.死信列表())

    @app.post("/api/死信/重投")
    def api_死信重投():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.重投(body["事件号"]))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/死信/作废")
    def api_死信作废():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(死信.作废(body["事件号"], body.get("原因", "人工作废")))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/端点")
    def api_端点():
        return jsonify(签名目录.端点列表())

    @app.get("/api/端点/<key>")
    def api_端点详情(key):
        row = 签名目录.端点一条(key)
        if not row:
            return jsonify({"错误": "无此端点"}), 404
        return jsonify(row)

    @app.post("/api/端点")
    def api_端点登记():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.登记(body))
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/端点/启用")
    def api_端点启用():
        body = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(端点.改启用(body["端点键"], bool(body.get("启用"))))
        except (KeyError, ValueError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/互证")
    def api_互证列表():
        return jsonify(签名目录.互证列表())

    @app.post("/api/互证/跑")
    def api_互证跑():
        return jsonify(互证.跑一次())

    # —— hook-15 日切闭窗硬闸：闭窗灯 / 票水位 / 中文旁路 都从这一个同源状态口读 ——
    @app.get("/api/日切闭窗")
    def api_日切闭窗状态():
        return jsonify(日切闭窗.状态())

    @app.get("/api/闭窗")
    def api_闭窗状态别名():
        return jsonify(日切闭窗.状态())

    @app.get("/api/闭窗旁路")
    def api_闭窗旁路():
        return jsonify({"旁路": 日切闭窗.旁路列表(50)})

    def _解析闭窗意图(body) -> bool:
        if "闭窗" in body:
            v = body["闭窗"]
            if isinstance(v, bool):
                return v
            return str(v) in ("开", "1", "true", "True", "是", "闭窗", "on")
        if "打开" in body:  # “打开日切闭窗开关”= 落闸
            return bool(body["打开"])
        if "开关" in body:
            return str(body["开关"]) in ("开", "1", "true", "True", "是", "闭窗", "on")
        动作 = str(body.get("动作") or body.get("action") or "")
        if 动作 in ("close", "cut", "闭窗", "落闸", "打开"):
            return True
        if 动作 in ("open", "开窗", "解除", "release"):
            return False
        return True  # POST 空体默认落闸，方便页面单按钮

    @app.post("/api/日切闭窗")
    def api_日切闭窗设置():
        body = request.get_json(force=True, silent=True) or {}
        try:
            st = 日切闭窗.闭窗() if _解析闭窗意图(body) else 日切闭窗.开窗()
            return jsonify(st)
        except (ValueError, OSError) as e:
            return jsonify({"错误": str(e)}), 400

    @app.post("/api/闭窗")
    def api_闭窗设置别名():
        return api_日切闭窗设置()

    @app.post("/api/日切闭窗/闭窗")
    def api_日切闭窗落闸():
        return jsonify(日切闭窗.闭窗())

    @app.post("/api/日切闭窗/打开")
    def api_日切闭窗打开开关():
        return jsonify(日切闭窗.闭窗())

    @app.post("/api/日切闭窗/开窗")
    @app.post("/api/日切闭窗/解除")
    def api_日切闭窗开窗():
        return jsonify(日切闭窗.开窗())

    @app.post("/api/闭窗/打开")
    def api_闭窗打开别名():
        return jsonify(日切闭窗.闭窗())

    @app.post("/api/闭窗/解除")
    def api_闭窗解除别名():
        return jsonify(日切闭窗.开窗())

    @app.get("/api/hook-15")
    def api_hook15():
        st = 日切闭窗.状态()
        return jsonify({"骨签": 日切闭窗.骨签, **st})

    @app.get("/api/夹具")
    def api_夹具():
        return jsonify(夹具.列表())

    @app.post("/api/夹具入队/<name>")
    def api_夹具入队(name):
        try:
            体 = 夹具.读取(name)
            out = [入队.建事件(item) for item in 体.get("事件", [])]
            return jsonify({"入队": out})
        except FileNotFoundError:
            return jsonify({"错误": name}), 404
        except ValueError as e:
            return jsonify({"错误": str(e)}), 400

    @app.get("/api/空桩")
    def api_空桩():
        import importlib
        root = Path(__file__).resolve().parent / "modules"
        out = []
        已挂 = []
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if not (p.is_dir() and (p / "__init__.py").is_file()):
                    continue
                名 = p.name
                wired = False
                try:
                    mod = importlib.import_module(f"app.modules.{名}")
                    wired = bool(getattr(mod, "WIRED", False) or getattr(mod, "已挂硬实现", False))
                except Exception:
                    wired = False
                if wired:
                    已挂.append(名)
                else:
                    out.append(名)
        return jsonify({"空桩": out, "条数": len(out),
                        "已挂硬实现": 已挂, "已挂条数": len(已挂)})

    with app.app_context():
        seed.初始化库()
    return app
