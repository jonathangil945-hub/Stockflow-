import logging
import os

from flask import Flask, redirect, render_template, request, session, url_for

from config import Config
from app.db.migrations import run_migrations
from app.routes.admin_routes import admin_bp
from app.routes.auth_routes import auth_bp
from app.routes.bodega_routes import bodega_bp
from app.routes.cajero_routes import cajero_bp, wompi_webhook
from app.routes.etiquetas_routes import etiquetas_bp
from app.routes.reportes_routes import reportes_bp
from app.routes.superadmin_routes import superadmin_bp
from app.utils.security import effective_role, is_superadmin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if app.config["SECRET_KEY"].startswith("dev_"):
        app.logger.warning("SECRET_KEY no está definida en .env: usando una clave de desarrollo.")

    app.register_blueprint(auth_bp)
    app.register_blueprint(cajero_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(bodega_bp)
    app.register_blueprint(reportes_bp)
    app.register_blueprint(superadmin_bp)
    app.register_blueprint(etiquetas_bp)      # módulo de etiquetas QR (mini impresora)

    # ---------------------------------------------------------------
    # Compatibilidad con URLs antiguas de Wompi.
    # (Las rutas que había aquí usaban `db` —un módulo— como si fuera un
    #  diccionario y explotaban con TypeError; ahora todo vive en cajero_routes.)
    # ---------------------------------------------------------------
    @app.route("/payment-return")
    def legacy_payment_return():
        return redirect(url_for("cajero.payment_return", **request.args))

    app.add_url_rule("/webhook/wompi", "legacy_wompi_webhook", wompi_webhook, methods=["POST"])

    # Variables disponibles en todas las plantillas (menú según rol, etc.)
    @app.context_processor
    def inject_globals():
        return {
            "current_role": effective_role(),          # el súper admin dentro de una tienda = admin
            "is_superadmin": is_superadmin(),
            "current_business_name": session.get("business_name"),
            "current_username": session.get("username"),
            "low_stock_limit": app.config["LOW_STOCK_LIMIT"],
        }

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404,
                               message="La página que buscas no existe."), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", code=500,
                               message="Ocurrió un error inesperado. Intenta de nuevo."), 500

    if app.config.get("AUTO_MIGRATE"):
        run_migrations()

    return app


app = create_app()


if __name__ == "__main__":
    # 0.0.0.0 = accesible desde otros equipos/celulares de la red local
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )
