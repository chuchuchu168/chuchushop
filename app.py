import os, sqlite3, hashlib, secrets
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "database.db"))

# ── DB helpers ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        contact TEXT NOT NULL,
        phone TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        approved INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand TEXT NOT NULL,
        name TEXT NOT NULL,
        price INTEGER NOT NULL DEFAULT 0,
        image TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        barcode TEXT DEFAULT '',
        spec TEXT DEFAULT '',
        stock INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS brands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        logo TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS product_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand TEXT NOT NULL,
        product_name TEXT NOT NULL,
        image TEXT NOT NULL,
        UNIQUE(brand, product_name)
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        total INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT '待匯款',
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        product_id INTEGER NOT NULL REFERENCES products(id),
        qty INTEGER NOT NULL DEFAULT 1,
        price INTEGER NOT NULL DEFAULT 0
    );
    """)
    # Create default admin if not exists
    cur = db.execute("SELECT id FROM users WHERE is_admin=1")
    if not cur.fetchone():
        pw = hash_pw("Chuchu@2026!")
        db.execute(
            "INSERT INTO users(company,contact,phone,email,password_hash,approved,is_admin) VALUES(?,?,?,?,?,1,1)",
            ("管理員", "Admin", "0000000000", "admin@shop.com", pw),
        )
    db.commit()
    db.close()

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── Auth decorators ─────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not user or not user["approved"]:
            session.clear()
            flash("帳號尚未通過審核，請聯繫管理員", "warning")
            return redirect(url_for("login"))
        g.user = user
        return f(*a, **kw)
    return wrapped

def admin_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not user or not user["is_admin"]:
            flash("無權限", "danger")
            return redirect(url_for("catalog"))
        g.user = user
        return f(*a, **kw)
    return wrapped

# ── Auth routes ─────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        pw = hash_pw(request.form["password"])
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=? AND password_hash=?", (email, pw)).fetchone()
        if not user:
            flash("帳號或密碼錯誤", "danger")
            return redirect(url_for("login"))
        if not user["approved"]:
            flash("帳號尚未通過審核，請聯繫管理員", "warning")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]
        session["is_admin"] = user["is_admin"]
        return redirect(url_for("home"))
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        db = get_db()
        email = request.form["email"].strip()
        exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            flash("此 Email 已註冊", "danger")
            return redirect(url_for("register"))
        db.execute(
            "INSERT INTO users(company,contact,phone,email,password_hash) VALUES(?,?,?,?,?)",
            (
                request.form["company"].strip(),
                request.form["contact"].strip(),
                request.form["phone"].strip(),
                email,
                hash_pw(request.form["password"]),
            ),
        )
        db.commit()
        flash("註冊成功！請等待管理員審核", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── Home ────────────────────────────────────────────────────

@app.route("/")
def home():
    if "user_id" not in session:
        return render_template("home.html")
    return render_template("home_logged.html")

# ── Catalog ─────────────────────────────────────────────────

@app.route("/catalog")
@login_required
def catalog():
    db = get_db()
    # Get brand logos
    brand_logos = {}
    for b in db.execute("SELECT name, logo FROM brands").fetchall():
        brand_logos[b["name"]] = b["logo"]
    return render_template("catalog.html", brand_logos=brand_logos)

@app.route("/brand/<brand_name>")
@login_required
def brand_page(brand_name):
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT name FROM products WHERE brand=? AND active=1 ORDER BY name",
        (brand_name,),
    ).fetchall()
    product_names = [r["name"] for r in rows]
    # Get brand logo
    brand_logo = ""
    b = db.execute("SELECT logo FROM brands WHERE name=?", (brand_name,)).fetchone()
    if b:
        brand_logo = b["logo"]
    # Get product images
    product_images = {}
    for pi in db.execute("SELECT product_name, image FROM product_images WHERE brand=?", (brand_name,)).fetchall():
        product_images[pi["product_name"]] = pi["image"]
    return render_template("brand.html", brand_name=brand_name, brand_logo=brand_logo, product_names=product_names, product_images=product_images)

@app.route("/brand/<brand_name>/<product_name>")
@login_required
def product_page(brand_name, product_name):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products WHERE brand=? AND name=? AND active=1 ORDER BY spec",
        (brand_name, product_name),
    ).fetchall()
    return render_template("product.html", brand_name=brand_name, product_name=product_name, products=rows)

# ── Cart (session-based) ────────────────────────────────────

def get_cart():
    return session.get("cart", {})

def save_cart(cart):
    session["cart"] = cart

@app.route("/cart/add", methods=["POST"])
@login_required
def cart_add():
    pid = str(request.form["product_id"])
    qty = int(request.form.get("qty", 1))
    cart = get_cart()
    cart[pid] = cart.get(pid, 0) + qty
    save_cart(cart)
    flash("已加入購物車", "success")
    return redirect(url_for("catalog"))

@app.route("/cart/update", methods=["POST"])
@login_required
def cart_update():
    cart = get_cart()
    for key in list(cart.keys()):
        new_qty = request.form.get(f"qty_{key}", "0")
        new_qty = int(new_qty) if new_qty.isdigit() else 0
        if new_qty <= 0:
            del cart[key]
        else:
            cart[key] = new_qty
    save_cart(cart)
    flash("購物車已更新", "success")
    return redirect(url_for("cart_page"))

@app.route("/cart/remove/<pid>", methods=["POST"])
@login_required
def cart_remove(pid):
    cart = get_cart()
    cart.pop(str(pid), None)
    save_cart(cart)
    flash("已移除", "info")
    return redirect(url_for("cart_page"))

@app.route("/cart")
@login_required
def cart_page():
    db = get_db()
    cart = get_cart()
    items = []
    total = 0
    for pid, qty in cart.items():
        p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if p:
            subtotal = p["price"] * qty
            total += subtotal
            items.append({"product": p, "qty": qty, "subtotal": subtotal})
    return render_template("cart.html", items=items, total=total)

# ── Checkout / Orders ───────────────────────────────────────

@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    db = get_db()
    cart = get_cart()
    if not cart:
        flash("購物車是空的", "warning")
        return redirect(url_for("catalog"))

    items = []
    total = 0
    for pid, qty in cart.items():
        p = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if p:
            subtotal = p["price"] * qty
            total += subtotal
            items.append({"product": p, "qty": qty, "subtotal": subtotal})

    if request.method == "POST":
        note = request.form.get("note", "")
        cur = db.execute(
            "INSERT INTO orders(user_id,total,note) VALUES(?,?,?)",
            (session["user_id"], total, note),
        )
        order_id = cur.lastrowid
        for it in items:
            db.execute(
                "INSERT INTO order_items(order_id,product_id,qty,price) VALUES(?,?,?,?)",
                (order_id, it["product"]["id"], it["qty"], it["product"]["price"]),
            )
        db.commit()
        save_cart({})
        flash(f"訂單 #{order_id} 已成立，請盡快匯款", "success")
        return redirect(url_for("order_detail", oid=order_id))

    return render_template("checkout.html", items=items, total=total)

@app.route("/orders")
@login_required
def orders():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC",
        (session["user_id"],),
    ).fetchall()
    return render_template("orders.html", orders=rows)

@app.route("/orders/<int:oid>")
@login_required
def order_detail(oid):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (oid, session["user_id"])).fetchone()
    if not order:
        flash("找不到訂單", "danger")
        return redirect(url_for("orders"))
    items = db.execute("""
        SELECT oi.*, p.brand, p.name as product_name
        FROM order_items oi JOIN products p ON oi.product_id=p.id
        WHERE oi.order_id=?
    """, (oid,)).fetchall()
    return render_template("order_detail.html", order=order, items=items)

# ── Admin ───────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    pending = db.execute("SELECT * FROM users WHERE approved=0 ORDER BY created_at DESC").fetchall()
    users = db.execute("SELECT * FROM users WHERE approved=1 ORDER BY company").fetchall()
    all_orders = db.execute("""
        SELECT o.*, u.company FROM orders o JOIN users u ON o.user_id=u.id
        ORDER BY o.created_at DESC
    """).fetchall()
    # Group products by brand -> product name
    brand_list = [r["name"] for r in db.execute("SELECT name FROM brands ORDER BY sort_order, name").fetchall()]
    all_brands = [r[0] for r in db.execute("SELECT DISTINCT brand FROM products ORDER BY brand").fetchall()]
    # merge: brands table first, then any extra from products
    for b in all_brands:
        if b not in brand_list:
            brand_list.append(b)
    return render_template("admin.html", pending=pending, users=users, orders=all_orders, brand_list=brand_list)

@app.route("/admin/products/<brand_name>")
@admin_required
def admin_brand_products(brand_name):
    db = get_db()
    product_names = [r[0] for r in db.execute(
        "SELECT DISTINCT name FROM products WHERE brand=? ORDER BY name", (brand_name,)
    ).fetchall()]
    return render_template("admin_brand.html", brand_name=brand_name, product_names=product_names)

@app.route("/admin/products/<brand_name>/<product_name>")
@admin_required
def admin_product_variants(brand_name, product_name):
    db = get_db()
    products = db.execute(
        "SELECT * FROM products WHERE brand=? AND name=? ORDER BY spec",
        (brand_name, product_name),
    ).fetchall()
    return render_template("admin_variants.html", brand_name=brand_name, product_name=product_name, products=products)

@app.route("/admin/approve/<int:uid>", methods=["POST"])
@admin_required
def admin_approve(uid):
    db = get_db()
    db.execute("UPDATE users SET approved=1 WHERE id=?", (uid,))
    db.commit()
    flash("已通過審核", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/reject/<int:uid>", methods=["POST"])
@admin_required
def admin_reject(uid):
    db = get_db()
    db.execute("DELETE FROM users WHERE id=? AND approved=0", (uid,))
    db.commit()
    flash("已拒絕", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/product/add", methods=["POST"])
@admin_required
def admin_product_add():
    db = get_db()
    db.execute(
        "INSERT INTO products(brand,name,barcode,spec,price,sort_order) VALUES(?,?,?,?,?,?)",
        (
            request.form["brand"].strip(),
            request.form["name"].strip(),
            request.form.get("barcode", "").strip(),
            request.form.get("spec", "").strip(),
            int(request.form["price"]),
            int(request.form.get("sort_order", 0)),
        ),
    )
    db.commit()
    flash("商品已新增", "success")
    brand = request.form["brand"].strip()
    return redirect(url_for("admin_brand_products", brand_name=brand))

@app.route("/admin/product/edit/<int:pid>", methods=["POST"])
@admin_required
def admin_product_edit(pid):
    db = get_db()
    db.execute(
        "UPDATE products SET brand=?, name=?, barcode=?, spec=?, price=?, sort_order=?, active=? WHERE id=?",
        (
            request.form["brand"].strip(),
            request.form["name"].strip(),
            request.form.get("barcode", "").strip(),
            request.form.get("spec", "").strip(),
            int(request.form["price"]),
            int(request.form.get("sort_order", 0)),
            int(request.form.get("active", 0)),
            pid,
        ),
    )
    db.commit()
    flash("商品已更新", "success")
    brand = request.form["brand"].strip()
    name = request.form["name"].strip()
    return redirect(url_for("admin_product_variants", brand_name=brand, product_name=name))

@app.route("/admin/product/delete/<int:pid>", methods=["POST"])
@admin_required
def admin_product_delete(pid):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    flash("商品已刪除", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/order/<int:oid>/status", methods=["POST"])
@admin_required
def admin_order_status(oid):
    db = get_db()
    new_status = request.form["status"]
    db.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))
    db.commit()
    flash(f"訂單 #{oid} 狀態已更新為 {new_status}", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/order/<int:oid>")
@admin_required
def admin_order_detail(oid):
    db = get_db()
    order = db.execute("SELECT o.*, u.company, u.contact, u.phone, u.email FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=?", (oid,)).fetchone()
    if not order:
        flash("找不到訂單", "danger")
        return redirect(url_for("admin_dashboard"))
    items = db.execute("""
        SELECT oi.*, p.brand, p.name as product_name
        FROM order_items oi JOIN products p ON oi.product_id=p.id
        WHERE oi.order_id=?
    """, (oid,)).fetchall()
    return render_template("admin_order_detail.html", order=order, items=items)

# ── Init & Run ──────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
