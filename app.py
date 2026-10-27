#!/usr/bin/env python3
"""
Flujo de Caja (versión Streamlit) - cheques, cuentas por cobrar y por pagar.

Ejecutar con:   streamlit run flujo_caja_st.py
Requiere:       pip install streamlit pandas
Los datos se guardan en flujo_caja.db (junto a este archivo).
"""

import csv
import io
import os
import sqlite3

import pandas as pd
from datetime import date, datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flujo_caja.db")

# --------------------------------------------------------------------------
# Definición de los 4 tipos de movimiento
# --------------------------------------------------------------------------
# "ingreso": True suma al flujo, False resta.
# El primer estado de cada lista es el estado "abierto" (cuenta en la proyección);
# los demás estados se consideran cerrados y no entran en el flujo proyectado.
TIPOS = {
    "cheque_recibido": {
        "titulo": "Cheques recibidos",
        "ingreso": True,
        "estados": ["En cartera", "Depositado", "Endosado", "Rechazado"],
        "cerrado_ok": ["Depositado"],
        "contraparte": "Librador / Cliente",
        "numero": "N° de cheque",
        "banco": True,
        "fecha_venc": "Fecha de cobro",
        "boton_ok": "Marcar depositado",
        "estado_ok": "Depositado",
    },
    "cheque_emitido": {
        "titulo": "Cheques emitidos",
        "ingreso": False,
        "estados": ["Pendiente", "Debitado", "Anulado", "Rechazado"],
        "cerrado_ok": ["Debitado"],
        "contraparte": "Beneficiario / Proveedor",
        "numero": "N° de cheque",
        "banco": True,
        "fecha_venc": "Fecha de pago",
        "boton_ok": "Marcar debitado",
        "estado_ok": "Debitado",
    },
    "por_cobrar": {
        "titulo": "Cuentas por cobrar",
        "ingreso": True,
        "estados": ["Pendiente", "Cobrado", "Anulado"],
        "cerrado_ok": ["Cobrado"],
        "contraparte": "Cliente",
        "numero": "N° de factura / comprobante",
        "banco": False,
        "fecha_venc": "Fecha de vencimiento",
        "boton_ok": "Marcar cobrado",
        "estado_ok": "Cobrado",
        "periodo": True,  # permite cargar un período de facturación con varias facturas
    },
    "por_pagar": {
        "titulo": "Cuentas por pagar",
        "ingreso": False,
        "estados": ["Pendiente", "Pagado", "Anulado"],
        "cerrado_ok": ["Pagado"],
        "contraparte": "Proveedor",
        "numero": "N° de factura / comprobante",
        "banco": False,
        "fecha_venc": "Fecha de vencimiento",
        "boton_ok": "Marcar pagado",
        "estado_ok": "Pagado",
    },
}


# --------------------------------------------------------------------------
# Utilidades de formato (estilo argentino: 1.234,56 y dd/mm/aaaa)
# --------------------------------------------------------------------------
def parse_importe(texto):
    s = str(texto).strip().replace("$", "").replace(" ", "")
    if not s:
        raise ValueError("El importe está vacío.")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        valor = float(s)
    except ValueError:
        raise ValueError(f"Importe inválido: {texto!r}")
    if valor <= 0:
        raise ValueError("El importe debe ser mayor a cero.")
    return round(valor, 2)


def fmt_importe(valor):
    s = f"{valor:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def parse_fecha(texto, obligatoria=True):
    s = str(texto).strip()
    if not s:
        if obligatoria:
            raise ValueError("Falta una fecha obligatoria.")
        return ""
    for f in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Fecha inválida: {texto!r} (use dd/mm/aaaa)")


def fmt_fecha(iso):
    if not iso:
        return ""
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
def conectar(path=DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            numero TEXT DEFAULT '',
            contraparte TEXT DEFAULT '',
            banco TEXT DEFAULT '',
            fecha_emision TEXT DEFAULT '',
            fecha_venc TEXT NOT NULL,
            importe REAL NOT NULL,
            estado TEXT NOT NULL,
            observaciones TEXT DEFAULT ''
        )"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT)")
    conn.commit()
    migrar(conn)
    return conn


def migrar(conn):
    """Agrega las columnas de período si faltan (bases creadas con versiones anteriores).
    Es barata e idempotente: se llama también en cada ejecución de la app, porque
    Streamlit puede conservar una conexión en caché creada con el código viejo."""
    existentes = {r["name"] for r in conn.execute("PRAGMA table_info(movimientos)")}
    for col, ddl in (("periodo_desde", "TEXT DEFAULT ''"),
                     ("periodo_hasta", "TEXT DEFAULT ''"),
                     ("cant_facturas", "INTEGER DEFAULT 1")):
        if col not in existentes:
            conn.execute(f"ALTER TABLE movimientos ADD COLUMN {col} {ddl}")
    conn.commit()


def get_saldo_inicial(conn):
    r = conn.execute("SELECT valor FROM config WHERE clave='saldo_inicial'").fetchone()
    return float(r["valor"]) if r else 0.0


def set_saldo_inicial(conn, valor):
    conn.execute("INSERT OR REPLACE INTO config VALUES ('saldo_inicial', ?)", (str(valor),))
    conn.commit()


def alta(conn, tipo, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs,
         periodo_desde="", periodo_hasta="", cant_facturas=1):
    cur = conn.execute(
        """INSERT INTO movimientos
           (tipo, numero, contraparte, banco, fecha_emision, fecha_venc, importe, estado, observaciones,
            periodo_desde, periodo_hasta, cant_facturas)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tipo, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs,
         periodo_desde, periodo_hasta, cant_facturas),
    )
    conn.commit()
    return cur.lastrowid


def modificar(conn, id_, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs,
              periodo_desde="", periodo_hasta="", cant_facturas=1):
    conn.execute(
        """UPDATE movimientos SET numero=?, contraparte=?, banco=?, fecha_emision=?,
           fecha_venc=?, importe=?, estado=?, observaciones=?,
           periodo_desde=?, periodo_hasta=?, cant_facturas=? WHERE id=?""",
        (numero, contraparte, banco, f_emision, f_venc, importe, estado, obs,
         periodo_desde, periodo_hasta, cant_facturas, id_),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Carga masiva (períodos de facturación de varios clientes de una vez)
# --------------------------------------------------------------------------
ALIAS_COLUMNAS = {
    "cliente": "Cliente", "desde": "Desde", "hasta": "Hasta",
    "cant. facturas": "Cant", "cantidad": "Cant", "cant": "Cant", "cant facturas": "Cant",
    "importe total": "Importe", "importe": "Importe",
    "vencimiento": "Vencimiento", "detalle": "Detalle",
}

PLANTILLA_CSV = (
    "Cliente;Desde;Hasta;Cant. facturas;Importe total;Vencimiento;Detalle\n"
    "Cliente Ejemplo SA;01/10/2026;31/10/2026;5;1.250.000,00;10/11/2026;Facturas 0001-100 a 0001-104\n"
)


def _vacio(v):
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(v, str) and not v.strip()


def _txt(v):
    return "" if _vacio(v) else str(v).strip()


def _iso(v):
    """Convierte fecha (date, Timestamp o texto dd/mm/aaaa) a 'aaaa-mm-dd'. Vacío -> ''."""
    if _vacio(v):
        return ""
    if isinstance(v, str):
        return parse_fecha(v, obligatoria=False)
    return pd.Timestamp(v).date().isoformat()


def _importe(v):
    if isinstance(v, str):
        return parse_importe(v)
    if _vacio(v) or float(v) <= 0:
        raise ValueError("El importe debe ser mayor a cero.")
    return round(float(v), 2)


def _cant(v):
    if _vacio(v):
        return 1
    n = int(float(v)) if not isinstance(v, str) else int(float(v.replace(",", ".")))
    if n < 1:
        raise ValueError("La cantidad de facturas debe ser al menos 1.")
    return n


def leer_csv_masivo(data):
    """Lee bytes de un CSV (separador ; , o tab) y devuelve lista de dicts."""
    try:
        df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", dtype=str, encoding="latin-1")
    return df.to_dict("records")


def cargar_masivo(conn, tipo, filas, estado=None):
    """
    Inserta varias filas de una vez. Cada fila: dict con Cliente, Desde, Hasta, Cant (o
    'Cant. facturas'), Importe (o 'Importe total'), Vencimiento, Detalle.
    Todo o nada: si alguna fila tiene errores no se inserta ninguna.
    Devuelve (cantidad_insertada, lista_de_errores).
    """
    estado = estado or TIPOS[tipo]["estados"][0]
    hoy = date.today().isoformat()
    validas, errores = [], []
    for n, fila in enumerate(filas, start=1):
        f = {ALIAS_COLUMNAS.get(str(k).strip().lower(), k): v for k, v in fila.items()}
        if all(_vacio(v) for v in f.values()):
            continue  # fila totalmente en blanco
        try:
            cliente = _txt(f.get("Cliente"))
            if not cliente:
                raise ValueError("falta el cliente")
            try:
                importe = _importe(f.get("Importe"))
            except (ValueError, TypeError):
                raise ValueError("importe inválido o vacío")
            venc = _iso(f.get("Vencimiento"))
            if not venc:
                raise ValueError("falta la fecha de vencimiento")
            desde, hasta = _iso(f.get("Desde")), _iso(f.get("Hasta"))
            if desde and hasta and hasta < desde:
                raise ValueError("el período 'hasta' es anterior al 'desde'")
            cant = _cant(f.get("Cant"))
            validas.append((_txt(f.get("Detalle")), cliente, "", hoy, venc, importe, estado, "",
                            desde, hasta, cant))
        except (ValueError, TypeError) as e:
            errores.append(f"Fila {n}: {e}")
    if errores:
        return 0, errores
    for v in validas:
        alta(conn, tipo, *v)
    return len(validas), []


def cambiar_estado(conn, id_, estado):
    conn.execute("UPDATE movimientos SET estado=? WHERE id=?", (estado, id_))
    conn.commit()


def eliminar(conn, id_):
    conn.execute("DELETE FROM movimientos WHERE id=?", (id_,))
    conn.commit()


def listar(conn, tipo, solo_abiertos=False):
    sql = "SELECT * FROM movimientos WHERE tipo=?"
    params = [tipo]
    if solo_abiertos:
        sql += " AND estado=?"
        params.append(TIPOS[tipo]["estados"][0])
    sql += " ORDER BY fecha_venc, id"
    return conn.execute(sql, params).fetchall()


# --------------------------------------------------------------------------
# Cálculo del flujo de caja proyectado
# --------------------------------------------------------------------------
def _inicio_periodo(d, periodo):
    if periodo == "mensual":
        return d.replace(day=1)
    return d - timedelta(days=d.weekday())  # lunes


def _siguiente(d, periodo):
    if periodo == "mensual":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=7)


def resumen(conn, hoy=None):
    """Totales abiertos por tipo y montos vencidos."""
    hoy = (hoy or date.today()).isoformat()
    out = {}
    for tipo, cfg in TIPOS.items():
        abierto = cfg["estados"][0]
        r = conn.execute(
            "SELECT COALESCE(SUM(importe),0) t, COUNT(*) n FROM movimientos WHERE tipo=? AND estado=?",
            (tipo, abierto),
        ).fetchone()
        v = conn.execute(
            "SELECT COALESCE(SUM(importe),0) t, COUNT(*) n FROM movimientos "
            "WHERE tipo=? AND estado=? AND fecha_venc<?",
            (tipo, abierto, hoy),
        ).fetchone()
        out[tipo] = {"total": r["t"], "cant": r["n"], "vencido": v["t"], "cant_vencido": v["n"]}
    return out


def proyeccion(conn, periodo="semanal", cantidad=12, hoy=None):
    """
    Devuelve (filas, saldo_inicial). Cada fila:
    dict(etiqueta, ingresos, egresos, neto, saldo)
    Primera fila: 'Vencido' (todo lo abierto con fecha anterior a hoy).
    """
    hoy = hoy or date.today()
    saldo = get_saldo_inicial(conn)
    saldo0 = saldo

    abiertos = []
    for tipo, cfg in TIPOS.items():
        for r in conn.execute(
            "SELECT fecha_venc, importe FROM movimientos WHERE tipo=? AND estado=?",
            (tipo, cfg["estados"][0]),
        ):
            abiertos.append((r["fecha_venc"], r["importe"], cfg["ingreso"]))

    filas = []

    def fila(etiqueta, desde, hasta):
        nonlocal saldo
        ing = sum(i for f, i, es_ing in abiertos if es_ing and desde(f) and hasta(f))
        egr = sum(i for f, i, es_ing in abiertos if not es_ing and desde(f) and hasta(f))
        saldo += ing - egr
        filas.append({"etiqueta": etiqueta, "ingresos": ing, "egresos": egr,
                      "neto": ing - egr, "saldo": saldo})

    ini = _inicio_periodo(hoy, periodo)
    fila("Vencido", lambda f: True, lambda f: f < ini.isoformat())
    cur = ini
    for _ in range(cantidad):
        sig = _siguiente(cur, periodo)
        if periodo == "mensual":
            et = cur.strftime("%m/%Y")
        else:
            et = f"{cur.strftime('%d/%m')} al {(sig - timedelta(days=1)).strftime('%d/%m')}"
        a, b = cur.isoformat(), sig.isoformat()
        fila(et, lambda f, a=a: f >= a, lambda f, b=b: f < b)
        cur = sig
    ultimo = cur.isoformat()
    fila("Posterior", lambda f: f >= ultimo, lambda f: True)
    return filas, saldo0



# --------------------------------------------------------------------------
# Interfaz Streamlit
# --------------------------------------------------------------------------
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Flujo de Caja", page_icon="💰", layout="wide")


@st.cache_resource
def get_conn():
    return conectar()


conn = get_conn()
migrar(conn)  # por si la conexión en caché es anterior a las columnas de período
HOY = date.today()


def a_fecha(iso):
    return datetime.strptime(iso, "%Y-%m-%d").date() if iso else None


def pagina_flujo():
    st.title("💰 Flujo de caja")
    c1, c2 = st.columns([1, 1])
    saldo = c1.number_input("Saldo inicial en caja/bancos ($)", value=float(get_saldo_inicial(conn)),
                            step=1000.0, format="%.2f")
    if saldo != get_saldo_inicial(conn):
        set_saldo_inicial(conn, saldo)
    periodo = c2.selectbox("Período", ["semanal", "mensual"])

    r = resumen(conn)
    ing = r["cheque_recibido"]["total"] + r["por_cobrar"]["total"]
    egr = r["cheque_emitido"]["total"] + r["por_pagar"]["total"]
    m = st.columns(4)
    m[0].metric("Total a ingresar", f"$ {fmt_importe(ing)}")
    m[1].metric("Total a pagar", f"$ {fmt_importe(egr)}")
    m[2].metric("Neto", f"$ {fmt_importe(ing - egr)}")
    venc_ing = r["cheque_recibido"]["vencido"] + r["por_cobrar"]["vencido"]
    venc_egr = r["cheque_emitido"]["vencido"] + r["por_pagar"]["vencido"]
    m[3].metric("Vencido (cobrar / pagar)", f"$ {fmt_importe(venc_ing)} / $ {fmt_importe(venc_egr)}")

    st.subheader("Resumen por tipo")
    st.dataframe(pd.DataFrame([{
        "Tipo": cfg["titulo"], "Pendientes": r[t]["cant"],
        "Total pendiente": fmt_importe(r[t]["total"]),
        "Vencido": fmt_importe(r[t]["vencido"]),
    } for t, cfg in TIPOS.items()]), hide_index=True, use_container_width=True)

    st.subheader("Flujo proyectado")
    filas, _ = proyeccion(conn, periodo, 12 if periodo == "semanal" else 6)
    st.dataframe(pd.DataFrame([{
        "Período": f["etiqueta"], "Ingresos": fmt_importe(f["ingresos"]),
        "Egresos": fmt_importe(f["egresos"]), "Neto": fmt_importe(f["neto"]),
        "Saldo acumulado": fmt_importe(f["saldo"]),
    } for f in filas]), hide_index=True, use_container_width=True)
    st.caption("Incluye solo lo pendiente. 'Vencido' = pendientes con fecha anterior al período actual.")
    st.line_chart(pd.DataFrame({"Saldo acumulado": [f["saldo"] for f in filas]},
                               index=[f["etiqueta"] for f in filas]))


def flash(msg):
    st.session_state["_flash"] = msg
    st.rerun()


def pagina_tipo(tipo):
    cfg = TIPOS[tipo]
    per = cfg.get("periodo", False)
    st.title(cfg["titulo"])
    if "_flash" in st.session_state:
        st.success(st.session_state.pop("_flash"))

    # ---- alta individual ----
    with st.expander("➕ Agregar nuevo", expanded=False):
        with st.form(f"alta_{tipo}", clear_on_submit=True):
            c = st.columns(3)
            numero = c[0].text_input(
                "Facturas / detalle (opcional)" if per else cfg["numero"],
                help="Ej: Facturas 0001-100 a 0001-104" if per else None)
            contraparte = c[1].text_input(cfg["contraparte"])
            banco = c[2].text_input("Banco") if cfg["banco"] else ""
            if per:
                c = st.columns(4)
                p_desde = c[0].date_input("Período facturado desde", value=HOY.replace(day=1), format="DD/MM/YYYY")
                p_hasta = c[1].date_input("Período facturado hasta", value=HOY, format="DD/MM/YYYY")
                cant = c[2].number_input("Cantidad de facturas", min_value=1, value=1, step=1)
                importe = c[3].number_input("Importe total del período ($)", min_value=0.0, step=100.0, format="%.2f")
                c = st.columns(3)
                f_emi = c[0].date_input("Fecha de emisión (última factura)", value=HOY, format="DD/MM/YYYY")
                f_venc = c[1].date_input(cfg["fecha_venc"], value=HOY, format="DD/MM/YYYY")
                estado = c[2].selectbox("Estado", cfg["estados"])
                obs = st.text_input("Observaciones")
            else:
                p_desde = p_hasta = None
                cant = 1
                c = st.columns(3)
                f_emi = c[0].date_input("Fecha de emisión", value=HOY, format="DD/MM/YYYY")
                f_venc = c[1].date_input(cfg["fecha_venc"], value=HOY, format="DD/MM/YYYY")
                importe = c[2].number_input("Importe ($)", min_value=0.0, step=100.0, format="%.2f")
                c = st.columns([1, 2])
                estado = c[0].selectbox("Estado", cfg["estados"])
                obs = c[1].text_input("Observaciones")
            if st.form_submit_button("Agregar", type="primary"):
                if not contraparte.strip():
                    st.error(f"Falta completar: {cfg['contraparte']}.")
                elif importe <= 0:
                    st.error("El importe debe ser mayor a cero.")
                elif per and p_hasta < p_desde:
                    st.error("El período 'hasta' no puede ser anterior al 'desde'.")
                else:
                    alta(conn, tipo, numero.strip(), contraparte.strip(), banco.strip(),
                         f_emi.isoformat(), f_venc.isoformat(), round(importe, 2), estado, obs.strip(),
                         p_desde.isoformat() if per else "", p_hasta.isoformat() if per else "",
                         int(cant))
                    flash("Registro agregado.")

    # ---- carga masiva (solo tipos con período) ----
    if per:
        with st.expander("📥 Carga masiva de períodos (varios clientes de una vez)"):
            st.caption("Cada fila = un cliente y un período, con el importe total adeudado en ese período. "
                       "Podés pegar filas copiadas desde Excel o subir un CSV. "
                       "Si alguna fila tiene errores no se carga ninguna.")
            k = st.session_state.setdefault(f"mk_{tipo}", 0)
            base = pd.DataFrame({
                "Cliente": pd.Series(dtype="object"),
                "Desde": pd.Series(dtype="datetime64[ns]"),
                "Hasta": pd.Series(dtype="datetime64[ns]"),
                "Cant. facturas": pd.Series(dtype="float64"),
                "Importe total": pd.Series(dtype="float64"),
                "Vencimiento": pd.Series(dtype="datetime64[ns]"),
                "Detalle": pd.Series(dtype="object"),
            })
            ed = st.data_editor(
                base, num_rows="dynamic", hide_index=True, use_container_width=True,
                key=f"masivo_{tipo}_{k}",
                column_config={
                    "Desde": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Hasta": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Vencimiento": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Cant. facturas": st.column_config.NumberColumn(format="%d", min_value=1, step=1),
                    "Importe total": st.column_config.NumberColumn(format="$ %.2f", min_value=0.01),
                })
            if st.button("Cargar filas", type="primary", key=f"btn_masivo_{tipo}"):
                n, errs = cargar_masivo(conn, tipo, ed.to_dict("records"))
                if errs:
                    st.error("No se cargó nada. Corregí estas filas:\n\n- " + "\n- ".join(errs))
                elif n == 0:
                    st.info("No hay filas para cargar.")
                else:
                    st.session_state[f"mk_{tipo}"] = k + 1
                    flash(f"{n} períodos cargados.")

            st.markdown("**O subir un archivo CSV**")
            st.download_button("⬇️ Descargar plantilla CSV", PLANTILLA_CSV.encode("utf-8-sig"),
                               file_name="plantilla_cuentas_por_cobrar.csv", mime="text/csv",
                               key=f"tpl_{tipo}")
            up = st.file_uploader("Archivo CSV", type="csv", key=f"up_{tipo}")
            if up is not None and st.button("Cargar CSV", key=f"btn_csv_{tipo}"):
                try:
                    n, errs = cargar_masivo(conn, tipo, leer_csv_masivo(up.getvalue()))
                except Exception as e:  # archivo ilegible
                    n, errs = 0, [f"No se pudo leer el archivo: {e}"]
                if errs:
                    st.error("No se cargó nada:\n\n- " + "\n- ".join(errs))
                elif n == 0:
                    st.info("El archivo no tiene filas para cargar.")
                else:
                    flash(f"{n} períodos cargados desde el CSV.")

    # ---- tabla editable ----
    solo = st.checkbox("Solo pendientes", value=True, key=f"solo_{tipo}")
    rows = listar(conn, tipo, solo)
    abierto = cfg["estados"][0]
    en7 = HOY + timedelta(days=7)
    datos = []
    for r in rows:
        fv = a_fecha(r["fecha_venc"])
        alerta = ""
        if r["estado"] == abierto:
            alerta = "🔴 Vencido" if fv < HOY else ("🟡 Vence en 7 días" if fv <= en7 else "")
        datos.append({
            "id": r["id"], "Alerta": alerta, "Número": r["numero"], "Contraparte": r["contraparte"],
            "Banco": r["banco"], "Período desde": a_fecha(r["periodo_desde"]),
            "Período hasta": a_fecha(r["periodo_hasta"]), "Cant. facturas": r["cant_facturas"] or 1,
            "Emisión": a_fecha(r["fecha_emision"]), "Vencimiento": fv,
            "Importe": r["importe"], "Estado": r["estado"], "Observaciones": r["observaciones"],
            "Eliminar": False,
        })
    if not datos:
        st.info("No hay registros para mostrar.")
        return
    df = pd.DataFrame(datos)
    pend = [d for d in datos if d["Estado"] == abierto]
    total = sum(d["Importe"] for d in pend)
    resumen_txt = f"**Total pendiente: $ {fmt_importe(total)}**"
    if per:
        resumen_txt += f"  ·  {sum(d['Cant. facturas'] for d in pend)} facturas en {len(pend)} registros"
    st.markdown(resumen_txt)

    ocultas = ["id"]
    if not cfg["banco"]:
        ocultas.append("Banco")
    if not per:
        ocultas += ["Período desde", "Período hasta", "Cant. facturas"]
    editado = st.data_editor(
        df, key=f"ed_{tipo}", hide_index=True, use_container_width=True,
        disabled=["id", "Alerta"], column_order=[c for c in df.columns if c not in ocultas],
        column_config={
            "Período desde": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Período hasta": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Cant. facturas": st.column_config.NumberColumn(format="%d", min_value=1, step=1),
            "Emisión": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Vencimiento": st.column_config.DateColumn(cfg["fecha_venc"], format="DD/MM/YYYY", required=True),
            "Importe": st.column_config.NumberColumn(
                "Importe total" if per else "Importe", format="$ %.2f", min_value=0.01, required=True),
            "Estado": st.column_config.SelectboxColumn(options=cfg["estados"], required=True),
            "Contraparte": st.column_config.TextColumn(cfg["contraparte"]),
            "Número": st.column_config.TextColumn("Facturas / detalle" if per else cfg["numero"]),
        })
    st.caption("Editá las celdas directamente (por ejemplo, cambiá el Estado a "
               f"'{cfg['estado_ok']}'), tildá 'Eliminar' para borrar y presioná Guardar cambios.")

    def valores(row):
        return (_txt(row.get("Número")), _txt(row.get("Contraparte")), _txt(row.get("Banco")),
                _iso(row.get("Emisión")), _iso(row.get("Vencimiento")),
                None if _vacio(row.get("Importe")) else round(float(row.get("Importe")), 2),
                row.get("Estado"), _txt(row.get("Observaciones")),
                _iso(row.get("Período desde")), _iso(row.get("Período hasta")), _cant(row.get("Cant. facturas")))

    c1, c2 = st.columns([1, 1])
    if c1.button("💾 Guardar cambios", type="primary", key=f"save_{tipo}"):
        originales = {d["id"]: d for d in datos}
        cambios = borrados = 0
        hubo_error = False
        for _, row in editado.iterrows():
            rid = int(row["id"])
            if row["Eliminar"]:
                eliminar(conn, rid)
                borrados += 1
                continue
            try:
                nuevo = valores(row)
            except (ValueError, TypeError) as e:
                st.error(f"Registro {rid}: {e}")
                hubo_error = True
                continue
            if nuevo == valores(originales[rid]):
                continue
            if not nuevo[4] or not nuevo[5] or nuevo[5] <= 0:
                st.error(f"Registro {rid}: faltan fecha de vencimiento o importe válido.")
                hubo_error = True
                continue
            if nuevo[8] and nuevo[9] and nuevo[9] < nuevo[8]:
                st.error(f"Registro {rid}: el período 'hasta' es anterior al 'desde'.")
                hubo_error = True
                continue
            modificar(conn, rid, *nuevo)
            cambios += 1
        if hubo_error:
            st.warning(f"{cambios} modificados, {borrados} eliminados (hay registros con errores sin guardar).")
        else:
            flash(f"{cambios} modificados, {borrados} eliminados.")

    todos = pd.DataFrame([{
        "Número": r["numero"], "Contraparte": r["contraparte"], "Banco": r["banco"],
        **({"Período desde": fmt_fecha(r["periodo_desde"]), "Período hasta": fmt_fecha(r["periodo_hasta"]),
            "Cant. facturas": r["cant_facturas"]} if per else {}),
        "Emisión": fmt_fecha(r["fecha_emision"]), "Vencimiento": fmt_fecha(r["fecha_venc"]),
        "Importe": fmt_importe(r["importe"]), "Estado": r["estado"], "Observaciones": r["observaciones"],
    } for r in listar(conn, tipo)])
    c2.download_button("⬇️ Exportar CSV", todos.to_csv(index=False, sep=";").encode("utf-8-sig"),
                       file_name=f"{tipo}.csv", mime="text/csv", key=f"csv_{tipo}")


opciones = {"📊 Flujo de caja": None}
opciones.update({cfg["titulo"]: t for t, cfg in TIPOS.items()})
eleccion = st.sidebar.radio("Menú", list(opciones))
if opciones[eleccion] is None:
    pagina_flujo()
else:
    pagina_tipo(opciones[eleccion])
