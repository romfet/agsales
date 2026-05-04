import os
import json
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
import data_store
import analyzer
import column_mapper
import ai_chat

app = Flask(__name__)
app.secret_key = "sales-ai-mvp-secret-key-2025"

UPLOAD_FOLDER = "/app/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.context_processor
def inject_upload_info():
    return {
        "upload_info": data_store.get_upload_info(),
        "stock_info": data_store.get_stock_info(),
    }


# --- Pages ---

@app.route("/")
def index():
    if data_store.is_loaded():
        return redirect(url_for("order_page"))
    return redirect(url_for("upload_page"))


@app.route("/upload", methods=["GET"])
def upload_page():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    """Step 1: Upload file and detect column mapping."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не выбран"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Пустое имя файла"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, "current_data.xlsx")
    f.save(filepath)

    try:
        result = column_mapper.detect_mapping(filepath)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Ошибка анализа файла: {e}"}), 400


@app.route("/upload/confirm", methods=["POST"])
def upload_confirm():
    """Step 2: Confirm mapping and load data."""
    data = request.get_json()
    mapping = data.get("mapping", {})

    filepath = os.path.join(UPLOAD_FOLDER, "current_data.xlsx")
    if not os.path.exists(filepath):
        return jsonify({"error": "Сначала загрузите файл"}), 400

    # Build {internal_name: original_col_name} from the confirmed mapping
    column_mapping = {}
    for internal_name, original_col in mapping.items():
        if original_col:
            column_mapping[internal_name] = original_col

    try:
        info = data_store.load_excel(filepath, column_mapping=column_mapping)
        return jsonify(info)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Ошибка обработки файла: {e}"}), 400


@app.route("/upload/stock", methods=["POST"])
def upload_stock():
    """Upload optional 'Остатки в выборке' file. Loads immediately (no mapping step)."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не выбран"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Пустое имя файла"}), 400

    filepath = os.path.join(UPLOAD_FOLDER, "current_stock.xlsx")
    f.save(filepath)

    try:
        info = data_store.load_stock(filepath)
        return jsonify(info)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Ошибка обработки файла остатков: {e}"}), 400


@app.route("/upload/stock/clear", methods=["POST"])
def upload_stock_clear():
    data_store.clear_stock()
    return jsonify({"ok": True})


@app.route("/order")
def order_page():
    if not data_store.is_loaded():
        flash("Сначала загрузите данные из CRM.", "error")
        return redirect(url_for("upload_page"))

    return render_template(
        "order_input.html",
        clients=data_store.get_clients(),
        products_n3=data_store.get_products_n3(),
    )


# --- API ---

@app.route("/api/clients")
def api_clients():
    return jsonify(data_store.get_clients())


@app.route("/api/products")
def api_products():
    return jsonify(data_store.get_products_n3())


@app.route("/api/products_n4", methods=["POST"])
def api_products_n4():
    data = request.get_json()
    n3 = data.get("n3", "")
    return jsonify(data_store.get_products_n4(n3))


@app.route("/api/order-lines", methods=["POST"])
def api_order_lines():
    data = request.get_json()
    order_num = data.get("order_num", "")
    if not order_num:
        return jsonify({"error": "Номер заказа не указан"}), 400
    result = data_store.get_order_lines(order_num)
    if not result:
        return jsonify({"error": "Заказ не найден"}), 404
    return jsonify(result)


@app.route("/api/search-orders", methods=["POST"])
def api_search_orders():
    data = request.get_json()
    query = data.get("query", "")
    client = data.get("client", None)
    return jsonify(data_store.search_orders(query, client))


@app.route("/api/client-info", methods=["POST"])
def api_client_info():
    data = request.get_json()
    client = data.get("client", "")
    return jsonify({
        "niche": data_store.get_client_niche(client),
        "total_orders": data_store.get_client_total_orders(client),
    })


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json()
    client = data.get("client", "")
    order_lines = data.get("order_lines", [])

    if not client:
        return jsonify({"error": "Клиент не выбран"}), 400
    if not order_lines:
        return jsonify({"error": "Заявка пуста"}), 400

    try:
        result1 = analyzer.analyze_forgotten(client, order_lines)
        result2 = analyzer.analyze_niche(client, order_lines)

        return jsonify({
            "client": client,
            "niche": data_store.get_client_niche(client),
            "analysis1_forgotten": result1,
            "analysis2_niche": result2,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    user_message = data.get("message", "")
    history = data.get("history", [])

    if not user_message:
        return jsonify({"error": "Пустое сообщение"}), 400

    result = ai_chat.chat(history, user_message)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
