"""Obtención del texto de un extracto, sea PDF, imagen o texto plano.

Estrategia por capas, de mejor a peor calidad:
  PDF:     pdfplumber -> PyMuPDF -> pypdf -> extractor propio (sin dependencias)
  Imagen:  Tesseract vía pytesseract (requiere instalarlo en el sistema)
  PDF escaneado: se rasteriza y se manda a OCR

Todas las dependencias son opcionales: el programa avisa qué falta en lugar
de fallar con un error de importación.
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, field

from . import pdf_basico

EXTENSIONES_PDF = {".pdf"}
EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
EXTENSIONES_TEXTO = {".txt", ".text"}
EXTENSIONES_SOPORTADAS = EXTENSIONES_PDF | EXTENSIONES_IMAGEN | EXTENSIONES_TEXTO


@dataclass
class TextoExtraido:
    texto: str
    paginas: list[str] = field(default_factory=list)
    motor: str = ""
    advertencias: list[str] = field(default_factory=list)


def _hay(modulo: str) -> bool:
    try:
        return importlib.util.find_spec(modulo) is not None
    except (ImportError, ValueError):
        return False


def motores_disponibles() -> dict[str, bool]:
    """Qué herramientas opcionales están instaladas en esta máquina."""
    disponibles = {
        "pdfplumber": _hay("pdfplumber"),
        "pymupdf": _hay("fitz"),
        "pypdf": _hay("pypdf") or _hay("PyPDF2"),
        "pillow": _hay("PIL"),
        "pytesseract": _hay("pytesseract"),
        "pdf2image": _hay("pdf2image"),
        "openpyxl": _hay("openpyxl"),
    }
    disponibles["tesseract_binario"] = _tesseract_instalado()
    disponibles["ocr"] = disponibles["pytesseract"] and disponibles["tesseract_binario"]
    return disponibles


def _tesseract_instalado() -> bool:
    import shutil

    if shutil.which("tesseract"):
        return True
    try:  # respeta una ruta configurada a mano en pytesseract
        import pytesseract  # type: ignore

        comando = pytesseract.pytesseract.tesseract_cmd
        return bool(comando) and (
            os.path.isfile(comando) or shutil.which(comando) is not None
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Calidad del texto
# ---------------------------------------------------------------------------

def calidad_texto(texto: str) -> float:
    """Proporción de caracteres "sensatos". Detecta PDF con fuentes ilegibles."""
    if not texto:
        return 0.0
    utiles = sum(
        1 for c in texto if c.isalnum() or c in " .,;:-/$()%*#\n\t°ÁÉÍÓÚÑáéíóúñ"
    )
    return utiles / len(texto)


def _texto_pobre(texto: str, paginas: int) -> bool:
    """True si parece que el PDF es escaneado o ilegible."""
    limpio = re.sub(r"\s", "", texto)
    if len(limpio) < max(60, 25 * max(paginas, 1)):
        return True
    if calidad_texto(texto) < 0.80:
        return True
    if not re.search(r"\d", texto):
        return True
    return False


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf_con_pdfplumber(ruta: str) -> list[str]:
    import pdfplumber  # type: ignore

    paginas: list[str] = []
    with pdfplumber.open(ruta) as pdf:
        for pagina in pdf.pages:
            # layout=True conserva la separación de columnas de la tabla.
            texto = pagina.extract_text(layout=True, x_tolerance=1.5) or ""
            if not texto.strip():
                texto = pagina.extract_text() or ""
            paginas.append(texto)
    return paginas


def _pdf_con_pymupdf(ruta: str) -> list[str]:
    import fitz  # type: ignore

    paginas: list[str] = []
    with fitz.open(ruta) as documento:
        for pagina in documento:
            paginas.append(pagina.get_text("text") or "")
    return paginas


def _pdf_con_pypdf(ruta: str) -> list[str]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        from PyPDF2 import PdfReader  # type: ignore

    lector = PdfReader(ruta)
    return [(pagina.extract_text() or "") for pagina in lector.pages]


def _rasterizar(ruta: str, dpi: int) -> list[object]:
    """Convierte las páginas del PDF en imágenes para poder aplicar OCR."""
    if _hay("fitz"):
        import io

        import fitz  # type: ignore
        from PIL import Image  # type: ignore

        imagenes = []
        with fitz.open(ruta) as documento:
            for pagina in documento:
                pix = pagina.get_pixmap(dpi=dpi)
                imagenes.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        return imagenes
    if _hay("pdf2image"):
        from pdf2image import convert_from_path  # type: ignore

        return list(convert_from_path(ruta, dpi=dpi))
    raise RuntimeError(
        "Para hacer OCR de un PDF escaneado se necesita PyMuPDF o pdf2image. "
        "Instala con: pip install pymupdf"
    )


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _preparar_imagen(imagen: object) -> object:
    """Mejora el contraste y el tamaño para que el OCR lea mejor las cifras."""
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        return imagen

    img = imagen
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    ancho, alto = img.size
    if ancho < 1600:  # las capturas de pantalla de celular llegan pequeñas
        escala = min(3.0, 1600 / max(ancho, 1))
        img = img.resize((int(ancho * escala), int(alto * escala)), Image.LANCZOS)
    return img


def ocr_imagen(imagen: object, idioma: str = "spa") -> str:
    import pytesseract  # type: ignore

    preparada = _preparar_imagen(imagen)
    configuracion = "--psm 6 -c preserve_interword_spaces=1"
    try:
        return pytesseract.image_to_string(
            preparada, lang=idioma, config=configuracion
        )
    except Exception:
        # Si no está el paquete de idioma español, se intenta con el de fábrica.
        return pytesseract.image_to_string(preparada, config=configuracion)


def _ocr_archivo_imagen(ruta: str, idioma: str) -> list[str]:
    from PIL import Image  # type: ignore

    with Image.open(ruta) as imagen:
        return [ocr_imagen(imagen, idioma)]


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def extraer_texto(
    ruta: str,
    *,
    dpi: int = 300,
    idioma: str = "spa",
    forzar_ocr: bool = False,
) -> TextoExtraido:
    """Devuelve el texto del archivo junto con el motor usado y las advertencias."""
    if not os.path.isfile(ruta):
        raise FileNotFoundError(f"No existe el archivo: {ruta}")

    extension = os.path.splitext(ruta)[1].lower()
    advertencias: list[str] = []
    disponibles = motores_disponibles()

    if extension in EXTENSIONES_TEXTO:
        with open(ruta, "r", encoding="utf-8", errors="replace") as archivo:
            contenido = archivo.read()
        return TextoExtraido(contenido, [contenido], "texto plano", advertencias)

    if extension in EXTENSIONES_IMAGEN:
        if not disponibles["pillow"]:
            raise RuntimeError(
                "Para leer imágenes se necesita Pillow. Instala con: pip install pillow"
            )
        if not disponibles["ocr"]:
            faltantes = []
            if not disponibles["pytesseract"]:
                faltantes.append("pip install pytesseract")
            if not disponibles["tesseract_binario"]:
                faltantes.append(
                    "instalar el programa Tesseract OCR "
                    "(Windows: https://github.com/UB-Mannheim/tesseract/wiki · "
                    "macOS: brew install tesseract tesseract-lang · "
                    "Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-spa)"
                )
            raise RuntimeError(
                "Los archivos JPG/PNG requieren OCR y falta: " + " y ".join(faltantes)
            )
        paginas = _ocr_archivo_imagen(ruta, idioma)
        advertencias.append(
            "Texto obtenido por OCR: revisa las cifras contra el extracto original."
        )
        return TextoExtraido("\n".join(paginas), paginas, f"ocr:{idioma}", advertencias)

    if extension not in EXTENSIONES_PDF:
        raise RuntimeError(
            f"Extensión no soportada: {extension}. "
            f"Se aceptan: {', '.join(sorted(EXTENSIONES_SOPORTADAS))}"
        )

    paginas: list[str] = []
    motor = ""

    if not forzar_ocr:
        intentos = [
            ("pdfplumber", disponibles["pdfplumber"], _pdf_con_pdfplumber),
            ("pymupdf", disponibles["pymupdf"], _pdf_con_pymupdf),
            ("pypdf", disponibles["pypdf"], _pdf_con_pypdf),
            ("interno", True, pdf_basico.extraer),
        ]
        for nombre, disponible, funcion in intentos:
            if not disponible:
                continue
            try:
                candidato = funcion(ruta)
            except Exception as error:  # PDF corrupto o caso no soportado
                advertencias.append(f"El motor {nombre} falló: {error}")
                continue
            texto_candidato = "\n".join(candidato)
            if not _texto_pobre(texto_candidato, len(candidato)):
                paginas, motor = candidato, nombre
                break
            # Se guarda el mejor intento por si todos resultan pobres.
            if len("".join(candidato)) > len("".join(paginas)):
                paginas, motor = candidato, nombre

        if motor == "interno" and paginas:
            advertencias.append(
                "Se usó el lector de PDF interno. Para mayor precisión: "
                "pip install pdfplumber"
            )

    texto = "\n".join(paginas)
    necesita_ocr = forzar_ocr or _texto_pobre(texto, len(paginas))

    if necesita_ocr:
        if disponibles["ocr"]:
            try:
                imagenes = _rasterizar(ruta, dpi)
                paginas_ocr = [ocr_imagen(imagen, idioma) for imagen in imagenes]
                if len("".join(paginas_ocr)) > len(texto):
                    paginas, motor = paginas_ocr, f"ocr:{idioma}"
                    texto = "\n".join(paginas)
                    advertencias.append(
                        "El PDF venía escaneado: se leyó con OCR. "
                        "Revisa las cifras contra el original."
                    )
            except Exception as error:
                advertencias.append(f"No se pudo aplicar OCR al PDF: {error}")
        elif not texto.strip():
            raise RuntimeError(
                f"'{os.path.basename(ruta)}' parece un PDF escaneado (sin texto) y "
                "no hay OCR disponible. Instala: pip install pymupdf pytesseract "
                "y el programa Tesseract OCR."
            )
        else:
            advertencias.append(
                "El texto extraído es escaso o de baja calidad; puede ser un PDF "
                "escaneado. Instalar OCR mejoraría el resultado."
            )

    return TextoExtraido(texto, paginas, motor or "desconocido", advertencias)
