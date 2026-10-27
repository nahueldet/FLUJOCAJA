#!/usr/bin/env python3
"""
Flujo de Caja - control de cheques, cuentas por cobrar y cuentas por pagar.

- Cheques recibidos  (ingresos futuros)
- Cheques emitidos   (egresos futuros)
- Cuentas por cobrar (ingresos futuros)
- Cuentas por pagar  (egresos futuros)

Solo usa la biblioteca estándar de Python 3.8+ (tkinter + sqlite3).
Los datos se guardan en el archivo flujo_caja.db (junto a este script).

Uso:  python flujo_caja.py
"""

import csv
import os
import sqlite3
from datetime import date, datetime, timedelta

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:  # permite importar la lógica sin entorno gráfico
    tk = None

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
    conn = sqlite3.connect(path)
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
# Interfaz gráfica
# --------------------------------------------------------------------------
if tk is not None:

    class PestanaMovimientos(ttk.Frame):
        def __init__(self, master, app, tipo):
            super().__init__(master, padding=8)
            self.app, self.tipo, self.cfg = app, tipo, TIPOS[tipo]
            self.conn = app.conn
            self.sel_id = None
            self._armar()
            self.refrescar()

        def _armar(self):
            cfg = self.cfg
            # --- Formulario ---
            form = ttk.LabelFrame(self, text=f"Datos - {cfg['titulo']}", padding=8)
            form.pack(fill="x")
            self.vars = {k: tk.StringVar() for k in
                         ("numero", "contraparte", "banco", "f_emision", "f_venc", "importe", "estado", "obs")}
            self.vars["estado"].set(cfg["estados"][0])
            self.vars["f_emision"].set(date.today().strftime("%d/%m/%Y"))

            campos = [
                (cfg["numero"], "numero"),
                (cfg["contraparte"], "contraparte"),
            ]
            if cfg["banco"]:
                campos.append(("Banco", "banco"))
            campos += [
                ("Fecha de emisión", "f_emision"),
                (cfg["fecha_venc"] + " (dd/mm/aaaa)", "f_venc"),
                ("Importe ($)", "importe"),
            ]
            for i, (lbl, key) in enumerate(campos):
                r, c = divmod(i, 3)
                ttk.Label(form, text=lbl).grid(row=r * 2, column=c, sticky="w", padx=4)
                ttk.Entry(form, textvariable=self.vars[key], width=28).grid(
                    row=r * 2 + 1, column=c, sticky="we", padx=4, pady=(0, 6))
            fila_extra = (len(campos) + 2) // 3 * 2
            ttk.Label(form, text="Estado").grid(row=fila_extra, column=0, sticky="w", padx=4)
            ttk.Combobox(form, textvariable=self.vars["estado"], values=cfg["estados"],
                         state="readonly", width=26).grid(row=fila_extra + 1, column=0, padx=4, sticky="we")
            ttk.Label(form, text="Observaciones").grid(row=fila_extra, column=1, sticky="w", padx=4)
            ttk.Entry(form, textvariable=self.vars["obs"], width=60).grid(
                row=fila_extra + 1, column=1, columnspan=2, padx=4, sticky="we")

            # --- Botones ---
            bt = ttk.Frame(self)
            bt.pack(fill="x", pady=6)
            ttk.Button(bt, text="Agregar", command=self.agregar).pack(side="left", padx=2)
            ttk.Button(bt, text="Guardar cambios", command=self.guardar).pack(side="left", padx=2)
            ttk.Button(bt, text=cfg["boton_ok"], command=self.marcar_ok).pack(side="left", padx=2)
            ttk.Button(bt, text="Eliminar", command=self.borrar).pack(side="left", padx=2)
            ttk.Button(bt, text="Limpiar", command=self.limpiar).pack(side="left", padx=2)
            ttk.Button(bt, text="Exportar CSV", command=self.exportar).pack(side="right", padx=2)
            self.solo_abiertos = tk.BooleanVar(value=True)
            ttk.Checkbutton(bt, text="Solo pendientes", variable=self.solo_abiertos,
                            command=self.refrescar).pack(side="right", padx=8)

            # --- Tabla ---
            cols = ("numero", "contraparte", "banco", "emision", "venc", "importe", "estado", "obs")
            heads = {"numero": "Número", "contraparte": cfg["contraparte"], "banco": "Banco",
                     "emision": "Emisión", "venc": cfg["fecha_venc"], "importe": "Importe",
                     "estado": "Estado", "obs": "Observaciones"}
            anchos = {"numero": 110, "contraparte": 190, "banco": 110, "emision": 85,
                      "venc": 110, "importe": 110, "estado": 90, "obs": 200}
            self.tree = ttk.Treeview(self, columns=cols, show="headings", height=12)
            for c in cols:
                self.tree.heading(c, text=heads[c])
                self.tree.column(c, width=anchos[c], anchor="e" if c == "importe" else "w")
            sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.tree.tag_configure("vencido", background="#f8d7da")
            self.tree.tag_configure("proximo", background="#fff3cd")
            self.tree.tag_configure("cerrado", foreground="#888888")
            self.tree.bind("<<TreeviewSelect>>", self.al_seleccionar)

            self.lbl_total = ttk.Label(self, text="")
            self.lbl_total.pack(side="bottom", anchor="e", pady=4)

        # ---- acciones ----
        def _leer(self):
            v = self.vars
            f_venc = parse_fecha(v["f_venc"].get())
            f_emi = parse_fecha(v["f_emision"].get(), obligatoria=False)
            importe = parse_importe(v["importe"].get())
            if not v["contraparte"].get().strip():
                raise ValueError(f"Falta completar: {self.cfg['contraparte']}.")
            return (v["numero"].get().strip(), v["contraparte"].get().strip(),
                    v["banco"].get().strip(), f_emi, f_venc, importe,
                    v["estado"].get(), v["obs"].get().strip())

        def agregar(self):
            try:
                datos = self._leer()
            except ValueError as e:
                return messagebox.showwarning("Revisar datos", str(e))
            alta(self.conn, self.tipo, *datos)
            self.limpiar()
            self.app.refrescar_todo()

        def guardar(self):
            if self.sel_id is None:
                return messagebox.showinfo("Guardar", "Seleccioná un registro de la tabla primero.")
            try:
                datos = self._leer()
            except ValueError as e:
                return messagebox.showwarning("Revisar datos", str(e))
            modificar(self.conn, self.sel_id, *datos)
            self.app.refrescar_todo()

        def marcar_ok(self):
            if self.sel_id is None:
                return messagebox.showinfo("Estado", "Seleccioná un registro de la tabla primero.")
            cambiar_estado(self.conn, self.sel_id, self.cfg["estado_ok"])
            self.limpiar()
            self.app.refrescar_todo()

        def borrar(self):
            if self.sel_id is None:
                return messagebox.showinfo("Eliminar", "Seleccioná un registro de la tabla primero.")
            if messagebox.askyesno("Eliminar", "¿Eliminar el registro seleccionado? No se puede deshacer."):
                eliminar(self.conn, self.sel_id)
                self.limpiar()
                self.app.refrescar_todo()

        def limpiar(self):
            self.sel_id = None
            for k, v in self.vars.items():
                v.set("")
            self.vars["estado"].set(self.cfg["estados"][0])
            self.vars["f_emision"].set(date.today().strftime("%d/%m/%Y"))
            self.tree.selection_remove(self.tree.selection())

        def al_seleccionar(self, _evt=None):
            sel = self.tree.selection()
            if not sel:
                return
            self.sel_id = int(sel[0])
            r = self.conn.execute("SELECT * FROM movimientos WHERE id=?", (self.sel_id,)).fetchone()
            v = self.vars
            v["numero"].set(r["numero"])
            v["contraparte"].set(r["contraparte"])
            v["banco"].set(r["banco"])
            v["f_emision"].set(fmt_fecha(r["fecha_emision"]))
            v["f_venc"].set(fmt_fecha(r["fecha_venc"]))
            v["importe"].set(fmt_importe(r["importe"]))
            v["estado"].set(r["estado"])
            v["obs"].set(r["observaciones"])

        def exportar(self):
            ruta = filedialog.asksaveasfilename(
                defaultextension=".csv", initialfile=f"{self.tipo}.csv",
                filetypes=[("CSV", "*.csv")])
            if not ruta:
                return
            with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Número", "Contraparte", "Banco", "Emisión", "Vencimiento",
                            "Importe", "Estado", "Observaciones"])
                for r in listar(self.conn, self.tipo):
                    w.writerow([r["numero"], r["contraparte"], r["banco"], fmt_fecha(r["fecha_emision"]),
                                fmt_fecha(r["fecha_venc"]), fmt_importe(r["importe"]),
                                r["estado"], r["observaciones"]])
            messagebox.showinfo("Exportar", f"Archivo guardado en:\n{ruta}")

        def refrescar(self):
            self.tree.delete(*self.tree.get_children())
            hoy = date.today().isoformat()
            en7 = (date.today() + timedelta(days=7)).isoformat()
            abierto = self.cfg["estados"][0]
            total = 0.0
            for r in listar(self.conn, self.tipo, self.solo_abiertos.get()):
                tags = ()
                if r["estado"] != abierto:
                    tags = ("cerrado",)
                else:
                    total += r["importe"]
                    if r["fecha_venc"] < hoy:
                        tags = ("vencido",)
                    elif r["fecha_venc"] <= en7:
                        tags = ("proximo",)
                self.tree.insert("", "end", iid=str(r["id"]), tags=tags, values=(
                    r["numero"], r["contraparte"], r["banco"], fmt_fecha(r["fecha_emision"]),
                    fmt_fecha(r["fecha_venc"]), fmt_importe(r["importe"]), r["estado"], r["observaciones"]))
            self.lbl_total.config(
                text=f"Total pendiente: $ {fmt_importe(total)}   (rojo = vencido, amarillo = vence en 7 días)")


    class PestanaFlujo(ttk.Frame):
        def __init__(self, master, app):
            super().__init__(master, padding=8)
            self.app, self.conn = app, app.conn
            self.periodo = tk.StringVar(value="semanal")
            self.saldo_var = tk.StringVar(value=fmt_importe(get_saldo_inicial(self.conn)))
            self._armar()
            self.refrescar()

        def _armar(self):
            top = ttk.Frame(self)
            top.pack(fill="x")
            ttk.Label(top, text="Saldo inicial en caja/bancos ($):").pack(side="left")
            ttk.Entry(top, textvariable=self.saldo_var, width=18).pack(side="left", padx=4)
            ttk.Button(top, text="Actualizar", command=self.guardar_saldo).pack(side="left", padx=4)
            ttk.Label(top, text="   Período:").pack(side="left")
            cb = ttk.Combobox(top, textvariable=self.periodo, values=["semanal", "mensual"],
                              state="readonly", width=10)
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self.refrescar())

            self.res = ttk.LabelFrame(self, text="Resumen de pendientes", padding=8)
            self.res.pack(fill="x", pady=8)
            self.lbl_res = ttk.Label(self.res, justify="left", font=("TkDefaultFont", 10))
            self.lbl_res.pack(anchor="w")

            cols = ("periodo", "ingresos", "egresos", "neto", "saldo")
            self.tree = ttk.Treeview(self, columns=cols, show="headings", height=15)
            for c, t in zip(cols, ("Período", "Ingresos", "Egresos", "Neto", "Saldo acumulado")):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=150, anchor="w" if c == "periodo" else "e")
            self.tree.pack(fill="both", expand=True)
            self.tree.tag_configure("neg", foreground="#b00020")
            self.tree.tag_configure("venc", background="#f8d7da")
            ttk.Label(self, text="Incluye solo cheques y cuentas pendientes. 'Vencido' = pendientes con fecha anterior "
                                 "al período actual (conviene regularizarlos).").pack(anchor="w", pady=4)

        def guardar_saldo(self):
            try:
                s = self.saldo_var.get().strip().replace("$", "").replace(" ", "")
                s = s.replace(".", "").replace(",", ".") if "," in s else s
                neg = s.startswith("-")
                valor = float(s)
            except ValueError:
                return messagebox.showwarning("Saldo", "Saldo inválido.")
            set_saldo_inicial(self.conn, valor)
            self.refrescar()

        def refrescar(self):
            self.tree.delete(*self.tree.get_children())
            self.saldo_var.set(fmt_importe(get_saldo_inicial(self.conn)))
            filas, _ = proyeccion(self.conn, self.periodo.get(), 12 if self.periodo.get() == "semanal" else 6)
            for f in filas:
                tags = []
                if f["saldo"] < 0:
                    tags.append("neg")
                if f["etiqueta"] == "Vencido":
                    tags.append("venc")
                self.tree.insert("", "end", tags=tags, values=(
                    f["etiqueta"], fmt_importe(f["ingresos"]), fmt_importe(f["egresos"]),
                    fmt_importe(f["neto"]), fmt_importe(f["saldo"])))
            r = resumen(self.conn)
            lineas = []
            for tipo, cfg in TIPOS.items():
                d = r[tipo]
                lineas.append(f"{cfg['titulo']:<22} {d['cant']:>3} pendientes   $ {fmt_importe(d['total']):>16}"
                              f"   (vencido: $ {fmt_importe(d['vencido'])})")
            ing = r["cheque_recibido"]["total"] + r["por_cobrar"]["total"]
            egr = r["cheque_emitido"]["total"] + r["por_pagar"]["total"]
            lineas.append(f"\nTotal a ingresar: $ {fmt_importe(ing)}    Total a pagar: $ {fmt_importe(egr)}"
                          f"    Neto: $ {fmt_importe(ing - egr)}")
            self.lbl_res.config(text="\n".join(lineas), font=("Courier", 10))


    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Flujo de Caja")
            self.geometry("1100x700")
            self.conn = conectar()
            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=6, pady=6)
            self.flujo = PestanaFlujo(nb, self)
            nb.add(self.flujo, text="Flujo de caja")
            self.pestanas = []
            for tipo, cfg in TIPOS.items():
                p = PestanaMovimientos(nb, self, tipo)
                nb.add(p, text=cfg["titulo"])
                self.pestanas.append(p)

        def refrescar_todo(self):
            for p in self.pestanas:
                p.refrescar()
            self.flujo.refrescar()


def main():
    if tk is None:
        raise SystemExit("Tkinter no está instalado. En Linux: sudo apt install python3-tk")
    App().mainloop()


if __name__ == "__main__":
    main()
