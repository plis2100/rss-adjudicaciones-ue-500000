import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests


# ============================================================
# CONFIGURACIÓN
# ============================================================

USUARIO_GITHUB = "plis2100"
REPOSITORIO_GITHUB = "rss-adjudicaciones-ue-500000"

URL_FEEDLY = (
    "https://raw.githubusercontent.com/"
    f"{USUARIO_GITHUB}/"
    f"{REPOSITORIO_GITHUB}/main/feed.xml"
)

API_TED = "https://api.ted.europa.eu/v3/notices/search"

ARCHIVO_RSS = Path("feed.xml")
ARCHIVO_HISTORIAL = Path("historial.json")

IMPORTE_MINIMO = 500_000.00
DIAS_BUSQUEDA = 10

RESULTADOS_POR_PAGINA = 100
MAXIMO_PAGINAS = 20
MAXIMO_ENTRADAS_RSS = 500

ZONA_HORARIA = ZoneInfo("Europe/Madrid")

# Todos estos campos existen en la API oficial de TED.
CAMPOS_TED = [
    "publication-number",
    "publication-date",
    "notice-title",
    "notice-type",
    "buyer-name",
    "buyer-country",
    "winner-name",
    "winner-identifier",
    "winner-decision-date",
    "result-value-notice",
    "result-value-cur-notice",
    "result-value-lot",
    "result-value-cur-lot",
    "total-value",
    "total-value-cur",
]

CABECERAS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; RSS-Adjudicaciones-UE/3.0; "
        "+https://github.com/plis2100)"
    ),
}


# ============================================================
# TEXTO
# ============================================================

def limpiar_texto(valor):
    if valor is None:
        return ""

    return " ".join(str(valor).split()).strip()


def extraer_textos(valor):
    if valor is None:
        return []

    if isinstance(valor, str):
        texto = limpiar_texto(valor)
        return [texto] if texto else []

    if isinstance(valor, (int, float)):
        return [str(valor)]

    if isinstance(valor, list):
        resultado = []

        for elemento in valor:
            for texto in extraer_textos(elemento):
                if texto and texto not in resultado:
                    resultado.append(texto)

        return resultado

    if isinstance(valor, dict):
        # Preferencia de idiomas.
        for idioma in ("spa", "es", "eng", "en"):
            if idioma in valor:
                textos = extraer_textos(valor[idioma])

                if textos:
                    return textos

        resultado = []

        for elemento in valor.values():
            for texto in extraer_textos(elemento):
                if texto and texto not in resultado:
                    resultado.append(texto)

        return resultado

    texto = limpiar_texto(valor)
    return [texto] if texto else []


def extraer_texto(valor):
    textos = extraer_textos(valor)
    return " / ".join(textos)


def valores_unicos(valores):
    resultado = []

    for valor in valores:
        valor = limpiar_texto(valor)

        if valor and valor not in resultado:
            resultado.append(valor)

    return resultado


# ============================================================
# IMPORTES
# ============================================================

def convertir_importe(valor):
    if valor is None:
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = limpiar_texto(valor)

    if not texto:
        return None

    texto = (
        texto.replace("\u00a0", "")
        .replace("EUR", "")
        .replace("€", "")
        .replace(" ", "")
    )

    texto = re.sub(r"[^0-9,.\-]", "", texto)

    if not texto:
        return None

    try:
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "")
                texto = texto.replace(",", ".")
            else:
                texto = texto.replace(",", "")

        elif "," in texto:
            partes = texto.split(",")

            if len(partes[-1]) in (1, 2):
                texto = texto.replace(".", "")
                texto = texto.replace(",", ".")
            else:
                texto = texto.replace(",", "")

        elif texto.count(".") > 1:
            texto = texto.replace(".", "")

        return float(texto)

    except ValueError:
        return None


def extraer_importes(valor):
    if valor is None:
        return []

    if isinstance(valor, (int, float)):
        return [float(valor)]

    if isinstance(valor, list):
        resultado = []

        for elemento in valor:
            resultado.extend(
                extraer_importes(elemento)
            )

        return resultado

    if isinstance(valor, dict):
        resultado = []

        for elemento in valor.values():
            resultado.extend(
                extraer_importes(elemento)
            )

        return resultado

    importe = convertir_importe(valor)

    return [importe] if importe is not None else []


def obtener_importe(noticia):
    campos = [
        "result-value-notice",
        "result-value-lot",
        "total-value",
    ]

    importes = []

    for campo in campos:
        importes.extend(
            extraer_importes(
                noticia.get(campo)
            )
        )

    importes = [
        importe
        for importe in importes
        if importe is not None and importe >= 0
    ]

    if not importes:
        return None

    return max(importes)


def obtener_monedas(noticia):
    campos = [
        "result-value-cur-notice",
        "result-value-cur-lot",
        "total-value-cur",
    ]

    monedas = []

    for campo in campos:
        textos = extraer_textos(
            noticia.get(campo)
        )

        for texto in textos:
            codigos = re.findall(
                r"\b[A-Z]{3}\b",
                texto.upper(),
            )

            for codigo in codigos:
                if codigo not in monedas:
                    monedas.append(codigo)

    return monedas


def formatear_importe(importe):
    texto = f"{importe:,.2f}"
    texto = texto.replace(",", "X")
    texto = texto.replace(".", ",")
    texto = texto.replace("X", ".")

    return f"{texto} €"


# ============================================================
# FECHAS
# ============================================================

def convertir_fecha(valor):
    textos = extraer_textos(valor)

    for texto in textos:
        texto = limpiar_texto(texto)

        if not texto:
            continue

        try:
            fecha = datetime.fromisoformat(
                texto.replace("Z", "+00:00")
            )

            if fecha.tzinfo is None:
                fecha = fecha.replace(
                    tzinfo=timezone.utc
                )

            return fecha.astimezone(
                ZONA_HORARIA
            )

        except ValueError:
            pass

        for formato in (
            "%Y-%m-%d",
            "%Y%m%d",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                fecha = datetime.strptime(
                    texto,
                    formato,
                )

                return fecha.replace(
                    tzinfo=ZONA_HORARIA
                )

            except ValueError:
                continue

    return None


def formatear_fecha(fecha):
    if fecha is None:
        return ""

    return fecha.astimezone(
        ZONA_HORARIA
    ).strftime("%d/%m/%Y")


def fecha_rss(fecha):
    if fecha is None:
        fecha = datetime.now(timezone.utc)

    if fecha.tzinfo is None:
        fecha = fecha.replace(
            tzinfo=timezone.utc
        )

    return format_datetime(
        fecha.astimezone(timezone.utc)
    )


# ============================================================
# API DE TED
# ============================================================

def solicitar_pagina(consulta, token=None):
    cuerpo = {
        "query": consulta,
        "fields": CAMPOS_TED,
        "limit": RESULTADOS_POR_PAGINA,
        "scope": "ALL",
        "checkQuerySyntax": False,
        "paginationMode": "ITERATION",
        "onlyLatestVersions": True,
    }

    if token:
        cuerpo["iterationNextToken"] = token

    ultimo_error = None

    for intento in range(1, 4):
        try:
            respuesta = requests.post(
                API_TED,
                headers=CABECERAS,
                json=cuerpo,
                timeout=(15, 60),
            )

            if respuesta.status_code == 429:
                espera = intento * 10

                print(
                    "TED ha limitado temporalmente "
                    f"la consulta. Esperando {espera}s."
                )

                time.sleep(espera)
                continue

            if respuesta.status_code >= 400:
                print(
                    "TED ha respondido con el código "
                    f"{respuesta.status_code}."
                )
                print(respuesta.text[:3000])

            respuesta.raise_for_status()

            datos = respuesta.json()

            if not isinstance(datos, dict):
                raise RuntimeError(
                    "TED no devolvió un objeto JSON."
                )

            return datos

        except (
            requests.RequestException,
            json.JSONDecodeError,
            RuntimeError,
        ) as error:
            ultimo_error = error

            print(
                f"Intento {intento} fallido: "
                f"{error}"
            )

            if intento < 3:
                espera = intento * 5

                print(
                    f"Reintentando dentro de "
                    f"{espera} segundos..."
                )

                time.sleep(espera)

    raise RuntimeError(
        f"No se pudo consultar TED: {ultimo_error}"
    )


def descargar_adjudicaciones():
    fecha_desde = (
        datetime.now(timezone.utc)
        - timedelta(days=DIAS_BUSQUEDA)
    ).strftime("%Y%m%d")

    # Sintaxis aceptada por el buscador experto de TED.
    consulta = (
        "("
        "notice-type=can-standard "
        "OR "
        "notice-type=can-social"
        ") "
        f"AND publication-date>={fecha_desde} "
        "SORT BY publication-date DESC"
    )

    print(f"Consulta TED: {consulta}")

    resultados = []
    token = None

    for pagina in range(
        1,
        MAXIMO_PAGINAS + 1,
    ):
        print(
            f"Descargando página TED "
            f"{pagina}/{MAXIMO_PAGINAS}"
        )

        datos = solicitar_pagina(
            consulta,
            token,
        )

        noticias = datos.get(
            "notices",
            [],
        )

        if not isinstance(noticias, list):
            noticias = []

        resultados.extend(noticias)

        print(
            f"Avisos recibidos: "
            f"{len(noticias)}"
        )

        nuevo_token = datos.get(
            "iterationNextToken"
        )

        if not nuevo_token:
            break

        if nuevo_token == token:
            break

        if len(noticias) < RESULTADOS_POR_PAGINA:
            break

        token = nuevo_token

    print(
        f"Total de avisos recibidos: "
        f"{len(resultados)}"
    )

    return resultados


# ============================================================
# TRANSFORMACIÓN
# ============================================================

def obtener_adjudicatario(noticia):
    nombres = valores_unicos(
        extraer_textos(
            noticia.get("winner-name")
        )
    )

    identificadores = valores_unicos(
        extraer_textos(
            noticia.get("winner-identifier")
        )
    )

    if not nombres:
        return ""

    adjudicatarios = []

    for posicion, nombre in enumerate(
        nombres[:5]
    ):
        resultado = nombre

        if posicion < len(identificadores):
            identificador = identificadores[posicion]

            if identificador not in nombre:
                resultado = (
                    f"{nombre} "
                    f"({identificador})"
                )

        if resultado not in adjudicatarios:
            adjudicatarios.append(resultado)

    return " / ".join(adjudicatarios)


def obtener_objeto(noticia):
    objeto = extraer_texto(
        noticia.get("notice-title")
    )

    if objeto:
        return objeto

    return "Adjudicación publicada en TED"


def transformar_noticia(noticia):
    numero = extraer_texto(
        noticia.get("publication-number")
    )

    if not numero:
        return None

    adjudicatario = obtener_adjudicatario(
        noticia
    )

    if not adjudicatario:
        return None

    importe = obtener_importe(noticia)

    if importe is None:
        return None

    if importe < IMPORTE_MINIMO:
        return None

    monedas = obtener_monedas(noticia)

    # No se comparan directamente importes en monedas
    # diferentes del euro.
    if monedas and "EUR" not in monedas:
        return None

    fecha_adjudicacion = convertir_fecha(
        noticia.get("winner-decision-date")
    )

    # Debe existir la fecha oficial de adjudicación.
    if fecha_adjudicacion is None:
        return None

    fecha_publicacion = convertir_fecha(
        noticia.get("publication-date")
    )

    objeto = obtener_objeto(noticia)

    comprador = extraer_texto(
        noticia.get("buyer-name")
    )

    pais = extraer_texto(
        noticia.get("buyer-country")
    )

    fecha_texto = formatear_fecha(
        fecha_adjudicacion
    )

    importe_texto = formatear_importe(
        importe
    )

    url = (
        "https://ted.europa.eu/es/notice/"
        f"-/detail/{numero}"
    )

    titulo = (
        "ADJUDICADA UE | "
        f"{fecha_texto} | "
        f"{adjudicatario} | "
        f"{importe_texto} | "
        f"{objeto}"
    )

    descripcion = [
        (
            "<p><strong>Estado:</strong> "
            "ADJUDICADA UE</p>"
        ),
        (
            "<p><strong>Fecha de adjudicación:"
            "</strong> "
            f"{html.escape(fecha_texto)}</p>"
        ),
        (
            "<p><strong>Adjudicatario:</strong> "
            f"{html.escape(adjudicatario)}</p>"
        ),
        (
            "<p><strong>Importe adjudicado:"
            "</strong> "
            f"{html.escape(importe_texto)}</p>"
        ),
        (
            "<p><strong>Objeto:</strong> "
            f"{html.escape(objeto)}</p>"
        ),
        (
            "<p><strong>Número TED:</strong> "
            f"{html.escape(numero)}</p>"
        ),
    ]

    if comprador:
        descripcion.append(
            (
                "<p><strong>Organismo contratante:"
                "</strong> "
                f"{html.escape(comprador)}</p>"
            )
        )

    if pais:
        descripcion.append(
            (
                "<p><strong>País:</strong> "
                f"{html.escape(pais)}</p>"
            )
        )

    descripcion.append(
        (
            f'<p><a href="{html.escape(url)}">'
            "Abrir adjudicación oficial en TED"
            "</a></p>"
        )
    )

    texto_id = (
        "adjudicada-ue-v3|"
        f"{numero}|"
        f"{fecha_texto}|"
        f"{adjudicatario}|"
        f"{importe:.2f}"
    )

    identificador = hashlib.sha256(
        texto_id.encode("utf-8")
    ).hexdigest()

    return {
        "id": identificador,
        "numero": numero,
        "titulo": titulo,
        "url": url,
        "descripcion": "".join(descripcion),
        "fecha_adjudicacion": (
            fecha_adjudicacion.isoformat()
        ),
        "fecha_publicacion": (
            fecha_publicacion.isoformat()
            if fecha_publicacion
            else ""
        ),
        "adjudicatario": adjudicatario,
        "importe": importe,
        "objeto": objeto,
    }


# ============================================================
# HISTORIAL
# ============================================================

def cargar_historial():
    if not ARCHIVO_HISTORIAL.exists():
        return []

    try:
        datos = json.loads(
            ARCHIVO_HISTORIAL.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(datos, list):
            return datos

    except (
        OSError,
        json.JSONDecodeError,
    ) as error:
        print(
            f"No se pudo leer el historial: "
            f"{error}"
        )

    return []


def guardar_historial(noticias):
    ARCHIVO_HISTORIAL.write_text(
        json.dumps(
            noticias[:MAXIMO_ENTRADAS_RSS],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def mezclar_noticias(nuevas, anteriores):
    por_id = {}

    for noticia in anteriores:
        identificador = noticia.get("id")

        if identificador:
            por_id[identificador] = noticia

    for noticia in nuevas:
        por_id[noticia["id"]] = noticia

    resultado = list(por_id.values())

    resultado.sort(
        key=lambda noticia: (
            noticia.get("fecha_adjudicacion")
            or noticia.get("fecha_publicacion")
            or ""
        ),
        reverse=True,
    )

    return resultado[:MAXIMO_ENTRADAS_RSS]


# ============================================================
# RSS
# ============================================================

def crear_rss(noticias):
    ET.register_namespace(
        "atom",
        "http://www.w3.org/2005/Atom",
    )

    # No se añade xmlns:atom manualmente.
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
        },
    )

    canal = ET.SubElement(
        rss,
        "channel",
    )

    ET.SubElement(
        canal,
        "title",
    ).text = (
        "Adjudicaciones UE superiores "
        "a 500.000 €"
    )

    ET.SubElement(
        canal,
        "link",
    ).text = (
        "https://ted.europa.eu/es/"
        "advanced-search"
    )

    ET.SubElement(
        canal,
        "description",
    ).text = (
        "Adjudicaciones publicadas en TED "
        "con fecha, adjudicatario e importe "
        "igual o superior a 500.000 euros."
    )

    ET.SubElement(
        canal,
        "language",
    ).text = "es"

    ET.SubElement(
        canal,
        "lastBuildDate",
    ).text = fecha_rss(
        datetime.now(timezone.utc)
    )

    ET.SubElement(
        canal,
        "ttl",
    ).text = "300"

    ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
        {
            "href": URL_FEEDLY,
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for noticia in noticias:
        item = ET.SubElement(
            canal,
            "item",
        )

        ET.SubElement(
            item,
            "title",
        ).text = noticia["titulo"]

        ET.SubElement(
            item,
            "link",
        ).text = noticia["url"]

        ET.SubElement(
            item,
            "guid",
            {
                "isPermaLink": "false",
            },
        ).text = noticia["id"]

        fecha = convertir_fecha(
            noticia.get("fecha_adjudicacion")
            or noticia.get("fecha_publicacion")
        )

        ET.SubElement(
            item,
            "pubDate",
        ).text = fecha_rss(fecha)

        ET.SubElement(
            item,
            "description",
        ).text = noticia["descripcion"]

        ET.SubElement(
            item,
            "category",
        ).text = "ADJUDICADA UE"

        ET.SubElement(
            item,
            "category",
        ).text = "Contratación pública"

        ET.SubElement(
            item,
            "category",
        ).text = "Más de 500.000 euros"

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")

    arbol.write(
        ARCHIVO_RSS,
        encoding="utf-8",
        xml_declaration=True,
    )

    # Comprueba inmediatamente que el XML es válido.
    ET.parse(ARCHIVO_RSS)

    print(
        f"feed.xml generado correctamente: "
        f"{ARCHIVO_RSS.stat().st_size} bytes"
    )


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    print("========================================")
    print("RSS DE ADJUDICACIONES DE LA UE")
    print("========================================")

    avisos = descargar_adjudicaciones()

    nuevas = []
    errores = 0

    for numero, aviso in enumerate(
        avisos,
        start=1,
    ):
        try:
            noticia = transformar_noticia(aviso)

            if noticia:
                nuevas.append(noticia)

        except Exception as error:
            errores += 1

            print(
                f"Error procesando aviso {numero}: "
                f"{type(error).__name__}: {error}"
            )

    anteriores = cargar_historial()

    resultado = mezclar_noticias(
        nuevas,
        anteriores,
    )

    # Estos archivos se crean incluso si no hay
    # adjudicaciones nuevas.
    guardar_historial(resultado)
    crear_rss(resultado)

    print("")
    print("========================================")
    print("PROCESO FINALIZADO CORRECTAMENTE")
    print("========================================")
    print(f"Avisos recibidos: {len(avisos)}")
    print(
        f"Adjudicaciones válidas nuevas: "
        f"{len(nuevas)}"
    )
    print(
        f"Entradas guardadas en el RSS: "
        f"{len(resultado)}"
    )
    print(f"Avisos con error: {errores}")
    print(f"URL para Feedly: {URL_FEEDLY}")

    if not nuevas:
        print("")
        print(
            "AVISO: el proceso ha funcionado, "
            "pero no encontró adjudicaciones nuevas "
            "en EUR superiores a 500.000 € que "
            "incluyeran adjudicatario y fecha."
        )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print(
            "Proceso cancelado.",
            file=sys.stderr,
        )
        sys.exit(130)

    except Exception as error:
        print(
            f"ERROR GENERAL: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
