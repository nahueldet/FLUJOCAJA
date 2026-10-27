#!/usr/bin/env python3
"""
Flujo de Caja (versión Streamlit) - cheques, cuentas por cobrar y por pagar.

Ejecutar con:   streamlit run flujo_caja_st.py
Requiere:       pip install streamlit pandas
Los datos se guardan en flujo_caja.db (junto a este archivo).
"""

import csv
import os
import sqlite3
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
    return conn


def get_saldo_inicial(conn):
    r = conn.execute("SELECT valor FROM config WHERE clave='saldo_inicial'").fetchone()
    return float(r["valor"]) if r else 0.0


def set_saldo_inicial(conn, valor):
    conn.execute("INSERT OR REPLACE INTO config VALUES ('saldo_inicial', ?)", (str(valor),))
    conn.commit()


def alta(conn, tipo, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs):
    cur = conn.execute(
        """INSERT INTO movimientos
           (tipo, numero, contraparte, banco, fecha_emision, fecha_venc, importe, estado, observaciones)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (tipo, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs),
    )
    conn.commit()
    return cur.lastrowid


def modificar(conn, id_, numero, contraparte, banco, f_emision, f_venc, importe, estado, obs):
    conn.execute(
        """UPDATE movimientos SET numero=?, contraparte=?, banco=?, fecha_emision=?,
           fecha_venc=?, importe=?, estado=?, observaciones=? WHERE id=?""",
        (numero, contraparte, banco, f_emision, f_venc, importe, estado, obs, id_),
    )
    conn.commit()


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


def pagina_tipo(tipo):
    cfg = TIPOS[tipo]
    st.title(cfg["titulo"])

    # ---- alta ----
    with st.expander("➕ Agregar nuevo", expanded=False):
        with st.form(f"alta_{tipo}", clear_on_submit=True):
            c = st.columns(3)
            numero = c[0].text_input(cfg["numero"])
            contraparte = c[1].text_input(cfg["contraparte"])
            banco = c[2].text_input("Banco") if cfg["banco"] else ""
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
                else:
                    alta(conn, tipo, numero.strip(), contraparte.strip(), banco.strip(),
                         f_emi.isoformat(), f_venc.isoformat(), round(importe, 2), estado, obs.strip())
                    st.success("Registro agregado.")
                    st.rerun()

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
            "Banco": r["banco"], "Emisión": a_fecha(r["fecha_emision"]), "Vencimiento": fv,
            "Importe": r["importe"], "Estado": r["estado"], "Observaciones": r["observaciones"],
            "Eliminar": False,
        })
    if not datos:
        st.info("No hay registros para mostrar.")
        return
    df = pd.DataFrame(datos)
    total = sum(d["Importe"] for d in datos if d["Estado"] == abierto)
    st.markdown(f"**Total pendiente: $ {fmt_importe(total)}**")

    cols_ocultas = ["id"] + ([] if cfg["banco"] else ["Banco"])
    editado = st.data_editor(
        df, key=f"ed_{tipo}", hide_index=True, use_container_width=True,
        disabled=["id", "Alerta"], column_order=[c for c in df.columns if c not in cols_ocultas],
        column_config={
            "Emisión": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Vencimiento": st.column_config.DateColumn(cfg["fecha_venc"], format="DD/MM/YYYY", required=True),
            "Importe": st.column_config.NumberColumn(format="$ %.2f", min_value=0.01, required=True),
            "Estado": st.column_config.SelectboxColumn(options=cfg["estados"], required=True),
            "Contraparte": st.column_config.TextColumn(cfg["contraparte"]),
            "Número": st.column_config.TextColumn(cfg["numero"]),
        })
    st.caption("Editá las celdas directamente (por ejemplo, cambiá el Estado a "
               f"'{cfg['estado_ok']}'), tildá 'Eliminar' para borrar y presioná Guardar cambios.")

    c1, c2 = st.columns([1, 1])
    if c1.button("💾 Guardar cambios", type="primary", key=f"save_{tipo}"):
        cambios = borrados = 0
        for _, row in editado.iterrows():
            if row["Eliminar"]:
                eliminar(conn, int(row["id"]))
                borrados += 1
                continue
            orig = df[df["id"] == row["id"]].iloc[0]
            if not row.drop(["Eliminar"]).equals(orig.drop(["Eliminar"])):
                fe = pd.Timestamp(row["Emisión"]).date().isoformat() if pd.notna(row["Emisión"]) else ""
                fv = pd.Timestamp(row["Vencimiento"]).date().isoformat() if pd.notna(row["Vencimiento"]) else None
                if not fv or not row["Importe"] or row["Importe"] <= 0:
                    st.error(f"Fila {int(row['id'])}: faltan fecha o importe válidos.")
                    continue
                modificar(conn, int(row["id"]), str(row["Número"] or ""), str(row["Contraparte"] or ""),
                          str(row["Banco"] or ""), fe, fv, round(float(row["Importe"]), 2),
                          row["Estado"], str(row["Observaciones"] or ""))
                cambios += 1
        st.success(f"{cambios} modificados, {borrados} eliminados.")
        st.rerun()

    todos = pd.DataFrame([{
        "Número": r["numero"], "Contraparte": r["contraparte"], "Banco": r["banco"],
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
