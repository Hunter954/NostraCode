from datetime import datetime, date, timedelta
from decimal import Decimal
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, current_app, send_from_directory, send_file, session
from flask_login import login_user, logout_user, login_required, current_user
from .extensions import db, oauth
from .models import User, Project, Invoice, Payment, RailwayService, RailwayUsageSnapshot
from .services.mercadopago import create_payment_preference, fetch_payment
from .sync import clear_unpaid_project_invoices, sync_project_from_railway, sync_all_projects, current_billing_cycle, format_billing_period, invoice_payment_available, invoice_payable_date, refresh_invoice_status, brazil_now, brazil_today, _add_months, RAILWAY_BILLING_DAY, INVOICE_DAYS_BEFORE_BILLING_DAY, invoice_is_future_cycle
from werkzeug.utils import secure_filename
from authlib.integrations.base_client.errors import OAuthError
import os
import secrets
from io import BytesIO

bp = Blueprint("main", __name__)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def money(value):
    return Decimal(str(value or "0").replace(",", "."))


def google_oauth_configured():
    return bool(current_app.config.get("GOOGLE_CLIENT_ID") and current_app.config.get("GOOGLE_CLIENT_SECRET"))


def login_destination(user):
    return url_for("main.admin_dashboard" if user.is_admin else "main.client_dashboard")


def upsert_google_user_from_profile(profile):
    google_id = profile.get("sub")
    email = (profile.get("email") or "").lower().strip()
    name = profile.get("name") or email.split("@")[0]
    avatar_url = profile.get("picture")

    if not google_id or not email:
        raise ValueError("O Google não retornou e-mail/identificador suficientes para login.")

    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()

    if user:
        user.google_id = user.google_id or google_id
        user.avatar_url = avatar_url or user.avatar_url
        user.auth_provider = "google" if user.auth_provider != "email" else user.auth_provider
        if not user.name:
            user.name = name
        db.session.commit()
        return user

    return None


def save_project_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"png", "jpg", "jpeg", "webp", "gif"}:
        flash("Use uma imagem PNG, JPG, WEBP ou GIF.", "warning")
        return None
    upload_dir = current_app.config.get("PROJECT_UPLOAD_FOLDER") or os.path.join("/data", "uploads", "projects")
    os.makedirs(upload_dir, exist_ok=True)
    unique_name = f"{brazil_now().strftime('%Y%m%d%H%M%S%f')}-{filename}"
    file_storage.save(os.path.join(upload_dir, unique_name))
    return f"/uploads/projects/{unique_name}"




def refresh_invoice_collection(invoices):
    changed = False
    for invoice in invoices:
        old_status = invoice.status
        refresh_invoice_status(invoice)
        if invoice.status != old_status:
            changed = True
    if changed:
        db.session.commit()


def format_brl_plain(value):
    value = Decimal(value or "0.00")
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def build_receipt_pdf(invoice, payment):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 22 * mm
    y = height - margin

    logo_path = os.path.join(current_app.root_path, "static", "img", "logomarca-nostracodes-preta.png")
    if os.path.exists(logo_path):
        pdf.drawImage(logo_path, margin, y - 18 * mm, width=58 * mm, height=20 * mm, preserveAspectRatio=True, mask="auto")
    else:
        pdf.setFillColor(colors.black)
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(margin, y - 10 * mm, "Nostra Codes")

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawRightString(width - margin, y - 4 * mm, "RECIBO DE PAGAMENTO")
    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - margin, y - 11 * mm, f"Fatura #{invoice.number}")
    pdf.drawRightString(width - margin, y - 17 * mm, f"Emitido em {brazil_now().strftime('%d/%m/%Y')}")

    y -= 35 * mm
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(1)
    pdf.line(margin, y, width - margin, y)
    y -= 12 * mm

    def label_value(label, value, x, y_pos):
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(x, y_pos, label.upper())
        pdf.setFont("Helvetica", 11)
        pdf.drawString(x, y_pos - 6 * mm, str(value or "-"))

    left = margin
    right = width / 2 + 6 * mm
    label_value("Cliente", invoice.client.name, left, y)
    label_value("E-mail", invoice.client.email, right, y)
    y -= 18 * mm
    label_value("Empresa", invoice.client.company or "-", left, y)
    label_value("Documento", invoice.client.document or "-", right, y)
    y -= 20 * mm

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(margin, y, "Serviço pago")
    y -= 8 * mm
    label_value("Projeto", invoice.project.name, left, y)
    label_value("Período", invoice.period, right, y)
    y -= 18 * mm
    label_value("Descrição", f"Serviço Nostra Codes / infraestrutura Railway - {invoice.project.name}", left, y)
    label_value("Data de pagamento", payment.paid_at.strftime("%d/%m/%Y %H:%M") if payment and payment.paid_at else "-", right, y)
    y -= 24 * mm

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(margin, y, "Resumo de valores")
    y -= 8 * mm

    rows = [
        ("Custo Railway", invoice.railway_cost),
        ("Taxa de gestão", invoice.management_fee),
        ("Descontos", -Decimal(invoice.discounts or "0.00")),
        ("Multa/Juros", invoice.fines),
        ("Total pago", payment.amount if payment else invoice.total),
    ]

    table_x = margin
    table_w = width - (2 * margin)
    row_h = 10 * mm
    pdf.setStrokeColor(colors.black)
    for index, (name, amount) in enumerate(rows):
        if index == len(rows) - 1:
            pdf.setFont("Helvetica-Bold", 12)
            pdf.setLineWidth(1.2)
        else:
            pdf.setFont("Helvetica", 11)
            pdf.setLineWidth(.5)
        pdf.rect(table_x, y - row_h, table_w, row_h, stroke=1, fill=0)
        pdf.drawString(table_x + 4 * mm, y - 6.5 * mm, name)
        pdf.drawRightString(table_x + table_w - 4 * mm, y - 6.5 * mm, format_brl_plain(amount))
        y -= row_h

    y -= 14 * mm
    label_value("Método", payment.method if payment else "-", left, y)
    label_value("Código da transação", payment.transaction_code if payment else "-", right, y)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(margin, 24 * mm, "Nostra Codes")
    pdf.drawRightString(width - margin, 24 * mm, "Recibo gerado automaticamente pelo painel do cliente.")

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer


@bp.route("/uploads/projects/<path:filename>")
def uploaded_project_image(filename):
    upload_dir = current_app.config.get("PROJECT_UPLOAD_FOLDER") or os.path.join("/data", "uploads", "projects")
    return send_from_directory(upload_dir, filename, max_age=86400)




@bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@bp.route("/")
def index():
    # A logo sempre leva para a landing page, mesmo com usuário logado.
    recent_projects = Project.query.order_by(Project.created_at.desc()).limit(12).all()
    return render_template("landing.html", recent_projects=recent_projects)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if User.query.filter_by(email=request.form["email"].lower()).first():
            flash("Este e-mail já está cadastrado.", "danger")
            return redirect(url_for("main.register"))
        if not request.form.get("accepted_terms"):
            flash("Você precisa aceitar os termos.", "danger")
            return redirect(url_for("main.register"))
        user = User(
            name=request.form["name"],
            email=request.form["email"].lower(),
            company=request.form.get("company"),
            whatsapp=request.form.get("whatsapp"),
            document=request.form.get("document"),
            accepted_terms=True,
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Conta criada com sucesso.", "success")
        return redirect(url_for("main.client_dashboard"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].lower()).first()
        if not user or not user.check_password(request.form["password"]):
            flash("E-mail ou senha inválidos.", "danger")
            return redirect(url_for("main.login"))
        if not user.is_admin and not user.is_active_client:
            flash("Seu acesso está bloqueado. Fale com o suporte.", "danger")
            return redirect(url_for("main.login"))
        login_user(user)
        return redirect(login_destination(user))
    return render_template("auth/login.html")



@bp.route("/login/google")
def login_google():
    if current_user.is_authenticated:
        return redirect(login_destination(current_user))
    if not google_oauth_configured() or not hasattr(oauth, "google"):
        flash("Login com Google ainda não está configurado. Defina GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET.", "warning")
        return redirect(url_for("main.login"))
    redirect_uri = url_for("main.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route("/auth/google/callback")
def google_callback():
    if not google_oauth_configured() or not hasattr(oauth, "google"):
        flash("Login com Google ainda não está configurado.", "warning")
        return redirect(url_for("main.login"))

    try:
        token = oauth.google.authorize_access_token()
        profile = token.get("userinfo")
        if not profile:
            profile = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo").json()
    except OAuthError as exc:
        flash(f"Não foi possível entrar com Google: {exc.error}", "danger")
        return redirect(url_for("main.login"))
    except Exception:
        flash("Não foi possível concluir o login com Google. Tente novamente.", "danger")
        return redirect(url_for("main.login"))

    try:
        user = upsert_google_user_from_profile(profile)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("main.login"))

    if user:
        if not user.is_admin and not user.is_active_client:
            flash("Seu acesso está bloqueado. Fale com o suporte.", "danger")
            return redirect(url_for("main.login"))
        login_user(user)
        flash("Login com Google realizado com sucesso.", "success")
        return redirect(login_destination(user))

    session["google_signup"] = {
        "google_id": profile.get("sub"),
        "email": (profile.get("email") or "").lower().strip(),
        "name": profile.get("name") or "",
        "avatar_url": profile.get("picture") or "",
    }
    return redirect(url_for("main.complete_google_register"))


@bp.route("/register/google/complete", methods=["GET", "POST"])
def complete_google_register():
    profile = session.get("google_signup")
    if not profile:
        flash("Inicie o cadastro pelo botão Entrar com Google.", "warning")
        return redirect(url_for("main.register"))

    if request.method == "POST":
        if not request.form.get("accepted_terms"):
            flash("Você precisa aceitar os termos.", "danger")
            return redirect(url_for("main.complete_google_register"))

        existing = User.query.filter_by(email=profile["email"]).first()
        if existing:
            existing.google_id = existing.google_id or profile["google_id"]
            existing.avatar_url = profile.get("avatar_url") or existing.avatar_url
            db.session.commit()
            user = existing
        else:
            user = User(
                name=request.form.get("name") or profile.get("name") or profile["email"].split("@")[0],
                email=profile["email"],
                company=request.form.get("company"),
                whatsapp=request.form.get("whatsapp"),
                document=request.form.get("document"),
                accepted_terms=True,
                google_id=profile["google_id"],
                avatar_url=profile.get("avatar_url"),
                auth_provider="google",
            )
            user.set_password(secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.commit()

        session.pop("google_signup", None)
        login_user(user)
        flash("Conta criada com Google com sucesso.", "success")
        return redirect(login_destination(user))

    return render_template("auth/google_register_complete.html", profile=profile)


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("main.index"))



def build_upcoming_invoices(projects):
    """Preview next month invoices without creating DB records."""
    today = brazil_today()
    next_anchor = _add_months(date(today.year, today.month, min(RAILWAY_BILLING_DAY, 28)), 1)
    payable_date = next_anchor - timedelta(days=INVOICE_DAYS_BEFORE_BILLING_DAY)
    period_start = _add_months(next_anchor, -1)
    period = format_billing_period(period_start, next_anchor)
    previews = []
    for index, project in enumerate(projects, start=1):
        amount = Decimal(project.estimated_cost or project.current_cost or "0.00").quantize(Decimal("0.01"))
        previews.append(type("UpcomingInvoicePreview", (), {
            "id": 0,
            "number": f"PREV-{index:03d}",
            "project": project,
            "period": period,
            "railway_cost": amount,
            "management_fee": Decimal("0.00"),
            "discounts": Decimal("0.00"),
            "fines": Decimal("0.00"),
            "total": amount,
            "status": "prevista",
            "due_date": payable_date,
        })())
    return previews

@bp.route("/dashboard")
@login_required
def client_dashboard():
    projects = Project.query.filter_by(client_id=current_user.id).all()
    invoices = visible_recent_invoices(Invoice.query.filter_by(client_id=current_user.id), limit=5)
    total_current = sum([p.current_cost or 0 for p in projects])
    total_estimated = sum([p.estimated_cost or 0 for p in projects])
    open_invoices = Invoice.query.filter(Invoice.client_id == current_user.id, Invoice.status.in_(["pendente", "aguardando pagamento", "atrasado"])).all()
    refresh_invoice_collection(open_invoices)
    next_invoice = sorted(open_invoices, key=invoice_payable_date)[0] if open_invoices else None
    upcoming_invoices = build_upcoming_invoices(projects)
    return render_template("client/dashboard.html", projects=projects, invoices=invoices, upcoming_invoices=upcoming_invoices, total_current=total_current, total_estimated=total_estimated, next_invoice=next_invoice, invoice_payment_available=invoice_payment_available, invoice_payable_date=invoice_payable_date)


def build_usage_chart(snapshots, project):
    """Prepare Railway usage snapshots aggregated month by month."""
    month_points = {}
    for snapshot in reversed(snapshots):
        created_at = snapshot.created_at or brazil_now()
        month_key = created_at.strftime("%Y-%m")
        value = snapshot.estimated_cost if snapshot.estimated_cost not in (None, Decimal("0.00")) else snapshot.current_cost
        month_points[month_key] = {
            "label": created_at.strftime("%m/%Y"),
            "time": "",
            "full_label": created_at.strftime("%B/%Y"),
            "value": Decimal(value or "0.00"),
        }

    points = list(month_points.values())[-6:]

    if not points:
        fallback_value = Decimal(project.estimated_cost or project.current_cost or "0.00")
        points.append({
            "label": brazil_now().strftime("%m/%Y"),
            "time": "",
            "full_label": "Mês atual",
            "value": fallback_value,
        })

    max_value = max([point["value"] for point in points] + [Decimal("1.00")])
    chart_max = max_value * Decimal("1.18")
    if chart_max <= 0:
        chart_max = Decimal("1.00")

    for point in points:
        percentage = int((point["value"] / chart_max) * Decimal("100")) if chart_max else 0
        point["height"] = max(12, min(96, percentage)) if point["value"] > 0 else 8

    trend_percent = None
    if len(points) >= 2 and points[0]["value"] > 0:
        trend_percent = int(((points[-1]["value"] - points[0]["value"]) / points[0]["value"]) * Decimal("100"))

    return {
        "points": points,
        "max_value": chart_max,
        "trend_percent": trend_percent,
        "has_history": len(points) > 1,
    }

@bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    if not current_user.is_admin and project.client_id != current_user.id:
        abort(403)
    invoices = Invoice.query.filter_by(project_id=project.id).order_by(Invoice.created_at.desc()).all()
    snapshots = RailwayUsageSnapshot.query.filter_by(project_id=project.id).order_by(RailwayUsageSnapshot.created_at.desc()).limit(90).all()
    usage_chart = build_usage_chart(snapshots, project)
    cycle_start, cycle_end = current_billing_cycle()
    billing_period = format_billing_period(cycle_start, cycle_end)
    refresh_invoice_collection(invoices)
    return render_template("client/project_detail.html", project=project, invoices=invoices, snapshots=snapshots, usage_chart=usage_chart, billing_period=billing_period, billing_due_date=cycle_end, invoice_payment_available=invoice_payment_available, invoice_payable_date=invoice_payable_date)


@bp.route("/invoices")
@login_required
def invoices():
    query = Invoice.query
    if not current_user.is_admin:
        query = query.filter_by(client_id=current_user.id)
    invoices = query.order_by(Invoice.created_at.desc()).all()
    refresh_invoice_collection(invoices)
    return render_template("client/invoices.html", invoices=invoices, invoice_payment_available=invoice_payment_available, invoice_payable_date=invoice_payable_date)


@bp.route("/invoices/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if not current_user.is_admin and invoice.client_id != current_user.id:
        abort(403)
    refresh_invoice_collection([invoice])
    return render_template("client/invoice_detail.html", invoice=invoice, can_pay=invoice_payment_available(invoice), payable_date=invoice_payable_date(invoice))




@bp.route("/invoices/<int:invoice_id>/receipt")
@login_required
def invoice_receipt(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if not current_user.is_admin and invoice.client_id != current_user.id:
        abort(403)
    if invoice.status != "pago":
        flash("O recibo só fica disponível depois que a fatura é paga.", "warning")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    payment = Payment.query.filter_by(invoice_id=invoice.id).order_by(Payment.paid_at.desc()).first()
    pdf_file = build_receipt_pdf(invoice, payment)
    return send_file(
        pdf_file,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"recibo-fatura-{invoice.number}.pdf",
    )


@bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@login_required
def pay_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if not current_user.is_admin and invoice.client_id != current_user.id:
        abort(403)
    if not invoice_payment_available(invoice):
        flash(f"O pagamento desta fatura será liberado em {invoice_payable_date(invoice).strftime('%d/%m/%Y')}.", "warning")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    preference = create_payment_preference(invoice)
    invoice.payment_link = preference["payment_link"]
    invoice.mp_preference_id = preference["preference_id"]
    invoice.mp_external_reference = preference["external_reference"]
    invoice.status = "aguardando pagamento"
    db.session.commit()
    if preference.get("demo"):
        flash("Modo demo: configure MERCADO_PAGO_ACCESS_TOKEN para gerar checkout real.", "warning")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    return redirect(invoice.payment_link)


@bp.route("/payments")
@login_required
def payments():
    query = Payment.query.join(Invoice)
    if not current_user.is_admin:
        query = query.filter(Invoice.client_id == current_user.id)
    payments = query.order_by(Payment.paid_at.desc()).all()
    return render_template("client/payments.html", payments=payments)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form["name"]
        current_user.company = request.form.get("company")
        current_user.whatsapp = request.form.get("whatsapp")
        current_user.document = request.form.get("document")
        if request.form.get("password"):
            current_user.set_password(request.form["password"])
        db.session.commit()
        flash("Perfil atualizado.", "success")
        return redirect(url_for("main.profile"))
    return render_template("client/profile.html")


@bp.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    clients_count = User.query.filter_by(role="client").count()
    projects_count = Project.query.count()
    pending_invoices = Invoice.query.filter(Invoice.status.in_(["pendente", "aguardando pagamento", "atrasado"])).count()
    paid_total = db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0)).scalar()
    projects = Project.query.order_by(Project.created_at.desc()).limit(6).all()
    invoices = visible_recent_invoices(Invoice.query, limit=6)
    upcoming_invoices = build_upcoming_invoices(Project.query.order_by(Project.created_at.desc()).all())
    last_railway_sync = db.session.query(db.func.max(Project.last_sync_at)).scalar()
    railway_sync_errors = Project.query.filter(Project.sync_status == "erro").count()
    return render_template("admin/dashboard.html", clients_count=clients_count, projects_count=projects_count, pending_invoices=pending_invoices, paid_total=paid_total, projects=projects, invoices=invoices, upcoming_invoices=upcoming_invoices, last_railway_sync=last_railway_sync, railway_sync_errors=railway_sync_errors, invoice_payment_available=invoice_payment_available, invoice_payable_date=invoice_payable_date)


@bp.route("/admin/clients")
@login_required
@admin_required
def admin_clients():
    clients = User.query.filter_by(role="client").order_by(User.created_at.desc()).all()
    return render_template("admin/clients.html", clients=clients)


@bp.route("/admin/clients/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_client(user_id):
    client = User.query.get_or_404(user_id)
    if client.is_admin:
        flash("Não é possível bloquear um administrador por esta tela.", "warning")
        return redirect(url_for("main.admin_clients"))
    client.is_active_client = not client.is_active_client
    db.session.commit()
    flash("Status do cliente atualizado.", "success")
    return redirect(url_for("main.admin_clients"))


@bp.route("/admin/clients/<int:user_id>/update", methods=["POST"])
@login_required
@admin_required
def admin_client_update(user_id):
    client = User.query.get_or_404(user_id)
    if client.is_admin:
        flash("Administradores não podem ser editados por esta tela.", "warning")
        return redirect(url_for("main.admin_clients"))

    email = (request.form.get("email") or "").lower().strip()
    name = (request.form.get("name") or "").strip()

    if not name or not email:
        flash("Nome e e-mail são obrigatórios.", "danger")
        return redirect(url_for("main.admin_clients"))

    existing = User.query.filter(User.email == email, User.id != client.id).first()
    if existing:
        flash("Este e-mail já está em uso por outro cliente.", "danger")
        return redirect(url_for("main.admin_clients"))

    client.name = name
    client.email = email
    client.company = (request.form.get("company") or "").strip() or None
    client.whatsapp = (request.form.get("whatsapp") or "").strip() or None
    client.document = (request.form.get("document") or "").strip() or None
    client.is_active_client = bool(request.form.get("is_active_client"))

    password = request.form.get("password") or ""
    if password.strip():
        if len(password.strip()) < 6:
            flash("A nova senha precisa ter pelo menos 6 caracteres.", "danger")
            return redirect(url_for("main.admin_clients"))
        client.set_password(password.strip())
        client.auth_provider = "email"

    db.session.commit()
    flash("Dados do cliente atualizados com sucesso.", "success")
    return redirect(url_for("main.admin_clients"))


@bp.route("/admin/clients/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_client_delete(user_id):
    client = User.query.get_or_404(user_id)
    if client.is_admin:
        flash("Administradores não podem ser excluídos por esta tela.", "warning")
        return redirect(url_for("main.admin_clients"))

    projects = Project.query.filter_by(client_id=client.id).all()
    project_ids = [project.id for project in projects]
    invoices = Invoice.query.filter_by(client_id=client.id).all()
    invoice_ids = [invoice.id for invoice in invoices]

    if invoice_ids:
        Payment.query.filter(Payment.invoice_id.in_(invoice_ids)).delete(synchronize_session=False)
    if project_ids:
        Invoice.query.filter(Invoice.project_id.in_(project_ids)).delete(synchronize_session=False)
        RailwayUsageSnapshot.query.filter(RailwayUsageSnapshot.project_id.in_(project_ids)).delete(synchronize_session=False)
        RailwayService.query.filter(RailwayService.project_id.in_(project_ids)).delete(synchronize_session=False)
        Project.query.filter(Project.id.in_(project_ids)).delete(synchronize_session=False)
    Invoice.query.filter_by(client_id=client.id).delete(synchronize_session=False)

    db.session.delete(client)
    db.session.commit()
    flash("Cliente excluído com sucesso.", "success")
    return redirect(url_for("main.admin_clients"))


@bp.route("/admin/projects/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_project_new():
    clients = User.query.filter_by(role="client", is_active_client=True).all()
    if request.method == "POST":
        project = Project(
            client_id=int(request.form["client_id"]),
            name=request.form["name"],
            railway_project_id=(request.form.get("railway_project_id") or "").strip(),
            status=request.form.get("status", "ativo"),
            plan=request.form.get("plan"),
            image_url=(request.form.get("image_url") or "").strip() or save_project_image(request.files.get("image_file")),
            description=request.form.get("description"),
            tech_stack=request.form.get("tech_stack"),
            monthly_value=Decimal("0.00"),
            usage_limit=Decimal("0.00"),
            current_cost=Decimal("0.00"),
            estimated_cost=Decimal("0.00"),
            management_fee=Decimal("0.00"),
            notes=request.form.get("notes"),
        )
        db.session.add(project)
        db.session.commit()
        if project.railway_project_id:
            try:
                sync_project_from_railway(project)
                flash("Projeto criado e sincronizado com a Railway.", "success")
            except Exception as exc:
                project.sync_status = "erro"
                project.sync_error = str(exc)
                project.last_sync_at = brazil_now()
                db.session.commit()
                flash(f"Projeto criado, mas a sincronização Railway falhou: {exc}", "warning")
        else:
            flash("Projeto criado.", "success")
        return redirect(url_for("main.project_detail", project_id=project.id))
    return render_template("admin/project_form.html", clients=clients, project=None)


@bp.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_project_edit(project_id):
    project = Project.query.get_or_404(project_id)
    clients = User.query.filter_by(role="client").all()
    if request.method == "POST":
        old_railway_project_id = project.railway_project_id
        new_railway_project_id = (request.form.get("railway_project_id") or "").strip()

        project.client_id = int(request.form["client_id"])
        project.name = request.form["name"]
        project.railway_project_id = new_railway_project_id
        project.status = request.form.get("status")
        project.plan = request.form.get("plan")
        uploaded_image = save_project_image(request.files.get("image_file"))
        project.image_url = uploaded_image or (request.form.get("image_url") or "").strip() or project.image_url
        project.description = request.form.get("description")
        project.tech_stack = request.form.get("tech_stack")
        project.notes = request.form.get("notes")

        if old_railway_project_id != new_railway_project_id:
            clear_unpaid_project_invoices(project)
            project.railway_internal_name = None
            project.railway_environment_id = None
            project.railway_service_id = None
            project.public_url = None
            project.current_cost = Decimal("0.00")
            project.estimated_cost = Decimal("0.00")
            project.monthly_value = Decimal("0.00")
            project.management_fee = Decimal("0.00")
            project.last_cost_update = brazil_now()

        db.session.commit()

        if project.railway_project_id:
            try:
                sync_project_from_railway(project)
                flash("Projeto atualizado e sincronizado com a Railway.", "success")
            except Exception as exc:
                project.sync_status = "erro"
                project.sync_error = str(exc)
                project.last_sync_at = brazil_now()
                db.session.commit()
                flash(f"Projeto atualizado, mas a sincronização Railway falhou: {exc}", "warning")
        else:
            flash("Projeto atualizado.", "success")
        return redirect(url_for("main.project_detail", project_id=project.id))
    return render_template("admin/project_form.html", clients=clients, project=project)



@bp.route("/admin/projects/<int:project_id>/sync-railway", methods=["POST"])
@login_required
@admin_required
def admin_project_sync_railway(project_id):
    project = Project.query.get_or_404(project_id)
    try:
        sync_project_from_railway(project)
        if project.sync_status == "parcial":
            flash("Projeto sincronizado parcialmente. Confira o aviso nos detalhes.", "warning")
        else:
            flash("Projeto sincronizado com a Railway.", "success")
    except Exception as exc:
        project.sync_status = "erro"
        project.sync_error = str(exc)
        project.last_sync_at = brazil_now()
        db.session.commit()
        flash(f"Erro ao sincronizar Railway: {exc}", "danger")
    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.route("/admin/sync-railway", methods=["POST"])
@login_required
@admin_required
def admin_sync_all_railway():
    total, ok, failed = sync_all_projects()
    if failed:
        flash(f"Sincronização finalizada: {ok}/{total} projetos atualizados, {failed} com erro.", "warning")
    else:
        flash(f"Sincronização finalizada: {ok}/{total} projetos atualizados.", "success")
    return redirect(url_for("main.admin_dashboard"))

@bp.route("/admin/invoices/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_invoice_new():
    projects = Project.query.order_by(Project.name.asc()).all()
    if request.method == "POST":
        project = Project.query.get_or_404(int(request.form["project_id"]))
        railway_cost = money(request.form.get("railway_cost"))
        management_fee = money(request.form.get("management_fee"))
        discounts = money(request.form.get("discounts"))
        fines = money(request.form.get("fines"))
        total = railway_cost + management_fee + fines - discounts
        invoice = Invoice(
            number=request.form["number"],
            client_id=project.client_id,
            project_id=project.id,
            period=request.form["period"],
            railway_cost=railway_cost,
            management_fee=management_fee,
            discounts=discounts,
            fines=fines,
            total=total,
            status=request.form.get("status", "pendente"),
            due_date=datetime.strptime(request.form["due_date"], "%Y-%m-%d").date(),
        )
        db.session.add(invoice)
        db.session.commit()
        flash("Fatura criada.", "success")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    suggested_number = f"{brazil_today().year}-{Invoice.query.count()+1:04d}"
    return render_template("admin/invoice_form.html", projects=projects, suggested_number=suggested_number)


@bp.route("/admin/invoices/<int:invoice_id>/mark-paid", methods=["POST"])
@login_required
@admin_required
def admin_mark_paid(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    invoice.status = "pago"
    payment = Payment(
        invoice_id=invoice.id,
        amount=invoice.total,
        status="pago",
        method=request.form.get("method", "manual"),
        transaction_code=request.form.get("transaction_code", f"manual-{invoice.id}"),
        paid_at=brazil_now(),
    )
    db.session.add(payment)
    db.session.commit()
    flash("Fatura marcada como paga.", "success")
    return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))


@bp.route("/webhooks/mercadopago", methods=["POST", "GET"])
def mercadopago_webhook():
    payload = request.get_json(silent=True) or request.args.to_dict()
    payment_id = payload.get("data", {}).get("id") if isinstance(payload.get("data"), dict) else payload.get("id")
    topic = payload.get("type") or payload.get("topic")
    if not payment_id or topic not in ["payment", "payment.created", "payment.updated"]:
        return jsonify({"ok": True})
    payment_data = fetch_payment(payment_id)
    if not payment_data:
        return jsonify({"ok": True, "demo": True})
    external_reference = payment_data.get("external_reference")
    invoice = Invoice.query.filter_by(mp_external_reference=external_reference).first()
    if invoice and payment_data.get("status") == "approved":
        invoice.status = "pago"
        exists = Payment.query.filter_by(transaction_code=str(payment_id)).first()
        if not exists:
            db.session.add(Payment(
                invoice_id=invoice.id,
                amount=Decimal(str(payment_data.get("transaction_amount", invoice.total))),
                status="pago",
                method=payment_data.get("payment_method_id"),
                transaction_code=str(payment_id),
                receipt_url=payment_data.get("transaction_details", {}).get("external_resource_url"),
                paid_at=brazil_now(),
            ))
        db.session.commit()
    return jsonify({"ok": True})
