import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders
from dotenv import load_dotenv
from typing import List, Optional, Union

load_dotenv()


class  MailSender:
    """
    Clase genérica para enviar correos con distintos tipos de adjuntos, cuerpo en texto o HTML,
    y posibilidad de copiar a otros destinatarios (CC/BCC).
    """

    def __init__(self,
                 sender_email: Optional[str] = None,
                 sender_password: Optional[str] = None,
                 smtp_server: str = "smtp.gmail.com",
                 smtp_port: int = 587):
        self.SMTP_SERVER = smtp_server
        self.SMTP_PORT = smtp_port
        self.SENDER_EMAIL = sender_email or os.getenv("SENDER_EMAIL")
        self.SENDER_PASSWORD = sender_password or os.getenv("SENDER_PASSWORD")

    # ===============================================================
    def _adjuntar_archivo(self, message: MIMEMultipart, ruta_archivo: str):
        """Adjunta cualquier tipo de archivo (pdf, excel, zip, imagen, etc.)."""
        if not os.path.exists(ruta_archivo):
            raise FileNotFoundError(f"No se encontró el archivo: {ruta_archivo}")

        nombre = os.path.basename(ruta_archivo)
        extension = os.path.splitext(nombre)[1].lower()

        if extension in [".jpg", ".jpeg", ".png", ".gif"]:
            with open(ruta_archivo, "rb") as img:
                imagen = MIMEImage(img.read())
                imagen.add_header("Content-Disposition", "attachment", filename=nombre)
                message.attach(imagen)
        else:
            with open(ruta_archivo, "rb") as f:
                adjunto = MIMEBase("application", "octet-stream")
                adjunto.set_payload(f.read())
            encoders.encode_base64(adjunto)
            adjunto.add_header("Content-Disposition", f"attachment; filename={nombre}")
            message.attach(adjunto)

    # ===============================================================
    def _insertar_banner(self, message: MIMEMultipart, ruta_imagen: str):
        """Inserta un banner (imagen inline) dentro del cuerpo HTML del correo."""
        with open(ruta_imagen, "rb") as img:
            imagen = MIMEImage(img.read())
            imagen.add_header("Content-ID", "<banner>")
            imagen.add_header("Content-Disposition", "inline", filename=os.path.basename(ruta_imagen))
            message.attach(imagen)

    # ===============================================================
    def enviar_correo(self,
                      destinatarios: Union[str, List[str]],
                      asunto: str,
                      cuerpo_texto: Optional[str] = None,
                      cuerpo_html: Optional[str] = None,
                      banner: Optional[str] = None,
                      adjuntos: Optional[List[str]] = None,
                      cc: Optional[List[str]] = None,
                      bcc: Optional[List[str]] = None,
                      reply_to: Optional[str] = None):
        """
        Envía un correo electrónico personalizado.
        """

        # Normalizar destinatarios
        if isinstance(destinatarios, str):
            destinatarios = [destinatarios]

        todos_destinatarios = destinatarios + (cc or []) + (bcc or [])

        message = MIMEMultipart("related")
        message["From"] = self.SENDER_EMAIL
        message["To"] = ", ".join(destinatarios)
        if cc:
            message["Cc"] = ", ".join(cc)
        if reply_to:
            message["Reply-To"] = reply_to
        message["Subject"] = asunto

        # Cuerpo alternativo: texto y/o HTML
        parte_alternativa = MIMEMultipart("alternative")

        if cuerpo_texto:
            parte_alternativa.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))

        if cuerpo_html:
            parte_alternativa.attach(MIMEText(cuerpo_html, "html", "utf-8"))

        message.attach(parte_alternativa)

        # Si hay banner, insertarlo
        if banner:
            self._insertar_banner(message, banner)

        # Adjuntar archivos
        if adjuntos:
    # Si es string, convertirlo a lista
            if isinstance(adjuntos, str):
                adjuntos = [adjuntos]

            for archivo in adjuntos:
                self._adjuntar_archivo(message, archivo)

        # Enviar correo
        try:
            print(f"📨 Enviando correo a: {', '.join(destinatarios)}")
            with smtplib.SMTP(self.SMTP_SERVER, self.SMTP_PORT) as server:
                server.starttls()
                server.login(self.SENDER_EMAIL, self.SENDER_PASSWORD)
                server.sendmail(self.SENDER_EMAIL, todos_destinatarios, message.as_string())

            print("✅ Correo enviado correctamente.")
            return True, "Correo enviado exitosamente"

        except Exception as e:
            print(f"❌ Error al enviar correo: {e}")
            return False, str(e)
