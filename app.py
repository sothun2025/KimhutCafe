import os
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from flask import Flask, render_template, session, redirect, url_for, request, flash
from flask_mail import Mail, Message
import requests

from config import DevelopmentConfig, ProductionConfig, TestingConfig  # <-- new

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # ---- Config selection (from .env -> APP_ENV) ----
    env_name = (os.getenv("APP_ENV") or "development").lower()
    cfg = {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
    }.get(env_name, DevelopmentConfig)

    app.config.from_object(cfg)

    # Init Flask-Mail after config is loaded
    mail = Mail(app)

    # Load products from JSON
    with open(os.path.join(app.root_path, "products.json"), "r", encoding="utf-8") as f:
        PRODUCTS = json.load(f)

    # ----- Helpers -----
    def _money(x):
        return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def cart_items():
        cart = session.get("cart", {})
        items = []
        for pid, qty in cart.items():
            product = next((p for p in PRODUCTS if p["id"] == int(pid)), None)
            if product:
                line_total = _money(Decimal(product["price"]) * Decimal(qty))
                items.append({
                    "id": product["id"],
                    "name": product["name"],
                    "price": _money(product["price"]),
                    "qty": int(qty),
                    "line_total": line_total
                })
        subtotal = _money(sum(i["line_total"] for i in items))
        return items, subtotal

    def send_telegram(text):
        token = app.config.get("TELEGRAM_BOT_TOKEN")
        chat_id = app.config.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            app.logger.warning("[Telegram] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.ok:
                return True
            app.logger.error("[Telegram] %s %s", r.status_code, r.text)
        except Exception:
            app.logger.exception("[Telegram] send failed")
        return False

    def send_invoice_email(customer, items, subtotal):
        if not app.config.get("MAIL_USERNAME"):
            print("[Mail] MAIL settings not configured; skipping send.")
            return False

        lines = [f"{i['qty']} x {i['name']} @ ${i['price']} = ${i['line_total']}" for i in items]
        body = "\n".join(lines) + f"\n\nSubtotal: ${subtotal}\nTime: {datetime.now():%Y-%m-%d %H:%M}"

        msg = Message(
            subject="Your Kimhut Café Invoice",
            recipients=[customer["email"]],
            body=(
                f"Hello {customer['name']},\n\nThanks for your order!\n\n"
                f"{body}\n\nDelivery to:\n{customer['address']}\n"
                f"Phone: {customer['phone']}\n\n— Kimhut Café"
            ),
        )
        try:
            mail.send(msg)
            return True
        except Exception:
            app.logger.exception("[Mail] send failed")
            return False

    # ----- Routes -----

    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/about")
    def about():
        return render_template("about.html")

    # --- Email helper for Contact page ---
    def send_contact_ack(to_email: str, name: str, original_message: str):
        """Send an acknowledgment email to the customer."""
        if not to_email:
            return False
        if not app.config.get("MAIL_USERNAME"):
            print("[Mail] MAIL_* not configured; skipping customer ACK.")
            return False

        msg = Message(
            subject="We received your message — Kimhut Café",
            recipients=[to_email],
            body=(
                f"Hi {name},\n\n"
                "Thanks for reaching out to Kimhut Café. We’ve received your message:\n\n"
                f"\"{original_message}\"\n\n"
                "We’ll get back to you as soon as possible.\n\n— Kimhut Café"
            ),
        )
        try:
            mail.send(msg)
            return True
        except Exception as e:
            print("[Mail] Error sending contact ACK:", e)
            return False

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            message = request.form.get("message", "").strip()

            from html import escape
            tg_text = (
                "<b>Contact</b>\n"
                f"From: {escape(name)} &lt;{escape(email)}&gt;\n\n"
                f"{escape(message)}"
            )
            tg_ok = send_telegram(tg_text)
            ack_ok = send_contact_ack(email, name, message)

            if tg_ok or ack_ok:
                flash("Thanks! Your message was sent.", "success")
            else:
                flash("Message received, but notifications are not configured.", "success")

            return redirect(url_for("contact"))

        return render_template("contact.html")

    @app.context_processor
    def inject_nav_categories():
        cats = sorted(set(p["category"] for p in PRODUCTS))
        sel = (request.args.get("category") or "All")
        q = request.args.get("q", "")
        return {
            "categories_nav": cats,
            "selected_nav_category": sel,
            "search_q": q,
        }

    @app.route("/products")
    def products():
        selected = request.args.get("category", "All")
        q = (request.args.get("q") or "").strip().lower()

        items = PRODUCTS

        if selected != "All":
            items = [p for p in items if p["category"].lower() == selected.lower()]

        if q:
            items = [p for p in items if q in p["name"].lower()]

        categories = ["All"] + sorted(set(p["category"] for p in PRODUCTS))
        return render_template(
            "products.html",
            products=items,
            categories=categories,
            selected=selected
        )

    @app.route("/cart")
    def cart():
        items, subtotal = cart_items()
        return render_template("cart.html", items=items, subtotal=subtotal)

    @app.context_processor
    def inject_cart_count():
        cart = session.get("cart", {})
        try:
            count = sum(int(q) for q in cart.values())
        except Exception:
            count = 0
        return {"cart_count": count}

    # @app.route("/add-to-cart", methods=["POST"])
    # def add_to_cart():
    #     pid = request.form.get("product_id")
    #     qty = int(request.form.get("qty", "1"))
    #     cart = session.get("cart", {})
    #     cart[pid] = cart.get(pid, 0) + qty
    #     session["cart"] = cart
    #     flash("Item added to cart.", "success")
    #     return redirect(url_for("products"))
    # app.py (inside create_app)

    # app.py
    from flask import jsonify

    @app.route("/add-to-cart", methods=["POST"])
    def add_to_cart():
        pid = request.form.get("product_id")
        qty = int(request.form.get("qty", "1"))
        cart = session.get("cart", {})
        cart[pid] = cart.get(pid, 0) + qty
        session["cart"] = cart

        cart_count = sum(int(q) for q in cart.values() if str(q).isdigit())

        # If request came via fetch/AJAX, return JSON to trigger the modal
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.accept_json:
            return jsonify({"ok": True, "cart_count": cart_count})

        # Fallback for non-JS
        flash("Item added to cart.", "success")
        return redirect(url_for("products"))

    @app.route("/update-cart", methods=["POST"])
    def update_cart():
        cart = {}
        for key, val in request.form.items():
            if key.startswith("qty_"):
                pid = key.replace("qty_", "")
                try:
                    q = max(0, int(val))
                    if q > 0:
                        cart[pid] = q
                except:
                    pass
        session["cart"] = cart
        flash("Cart updated.", "success")
        return redirect(url_for("cart"))

    @app.route("/checkout/success")
    def checkout_success():
        return render_template("checkout_success.html")

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        items, subtotal = cart_items()
        if request.method == "POST":
            customer = {
                "name": request.form.get("name", ""),
                "address": request.form.get("address", ""),
                "email": request.form.get("email", ""),
                "phone": request.form.get("phone", ""),
            }

            order_text = [
                "<b>New Order</b>",
                f"Name: {customer['name']}",
                f"Email: {customer['email']}",
                f"Phone: {customer['phone']}",
                f"Address: {customer['address']}",
                "",
                "Items:",
            ] + [f"- {i['qty']} x {i['name']} (${i['line_total']})" for i in items] + [
                f"\nSubtotal: ${subtotal}"
            ]
            send_telegram("\n".join(order_text))
            send_invoice_email(customer, items, subtotal)

            session["cart"] = {}
            flash("Checkout successful! Thanks for your order.", "success")
            return redirect(url_for("checkout_success"))
        return render_template("checkout.html", items=items, subtotal=subtotal)

    return app

app = create_app()

if __name__ == "__main__":
    app.run()
