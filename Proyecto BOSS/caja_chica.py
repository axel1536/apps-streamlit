# caja_chica.py
import streamlit as st
import pandas as pd
import os
from datetime import datetime

DATA_FILE = "caja_chica/movimientos.csv"
COMPROBANTES_DIR = "caja_chica/comprobantes"

def inicializar_caja():
    os.makedirs(COMPROBANTES_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        columnas = ["fecha", "usuario", "tipo", "monto", "descripcion", "categoria", "comprobante", "estado", "aprobado_por"]
        pd.DataFrame(columns=columnas).to_csv(DATA_FILE, index=False)

def cargar_movimientos():
    inicializar_caja()
    return pd.read_csv(DATA_FILE)

def guardar_movimiento(mov):
    df = cargar_movimientos()
    df = pd.concat([df, pd.DataFrame([mov])], ignore_index=True)
    df.to_csv(DATA_FILE, index=False)

def calcular_totales():
    df = cargar_movimientos()
    ingresos = df[df["tipo"] == "ingreso"]["monto"].sum()
    egresos_aprobados = df[(df["tipo"] == "egreso") & (df["estado"] == "Aprobado")]["monto"].sum()
    saldo = ingresos - egresos_aprobados
    return ingresos, egresos_aprobados, saldo

def guardar_comprobante(archivo, usuario):
    if not archivo:
        return ""
    ext = os.path.splitext(archivo.name)[1]
    nombre = f"{usuario}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    ruta = os.path.join(COMPROBANTES_DIR, nombre)
    with open(ruta, "wb") as f:
        f.write(archivo.getbuffer())
    return ruta

def mostrar_caja_chica():
    inicializar_caja()
    usuario = st.session_state.get("usuario_logueado", "desconocido")
    es_jefe = st.session_state["auth"] == "jefe"

    # Totales arriba
    ingresos, egresos_aprobados, saldo = calcular_totales()
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Ingresos", f"S/ {ingresos:,.2f}", delta_color="normal")
    col2.metric("Total Egresos aprobados", f"S/ {egresos_aprobados:,.2f}", delta_color="inverse")
    col3.metric("Saldo actual", f"S/ {saldo:,.2f}", delta_color="normal")

    tab_reg, tab_mis, tab_apr = st.tabs(["Registrar", "Mis movimientos", "Aprobaciones"])

    with tab_reg:
        # Formulario para INGRESOS (solo jefe)
        st.markdown("### Registrar Ingreso (reposición)")
        if not es_jefe:
            st.info("Solo el jefe puede registrar ingresos/reposiciones")
        else:
            with st.form("form_ingreso"):
                monto_ing = st.number_input("Monto S/.", min_value=0.01, step=0.01, format="%.2f", key="monto_ing")
                desc_ing = st.text_input("Descripción / motivo", key="desc_ing")
                cat_ing = st.selectbox("Categoría", [
                    "Reposición fondo", "Transferencia banco", "Otros ingresos"
                ], key="cat_ing")
                comp_ing = st.file_uploader("Comprobante (opcional)", type=["jpg", "png", "pdf"], key="comp_ing")

                if st.form_submit_button("Registrar Ingreso", type="primary"):
                    if monto_ing > 0:
                        ruta = guardar_comprobante(comp_ing, usuario) if comp_ing else ""
                        mov = {
                            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "usuario": usuario,
                            "tipo": "ingreso",
                            "monto": monto_ing,
                            "descripcion": desc_ing,
                            "categoria": cat_ing,
                            "comprobante": ruta,
                            "estado": "Aprobado",
                            "aprobado_por": usuario
                        }
                        guardar_movimiento(mov)
                        st.success("Ingreso registrado correctamente")
                        st.rerun()
                    else:
                        st.error("El monto debe ser mayor a 0")

        st.divider()

        # Formulario para EGRESOS (todos)
        st.markdown("### Registrar Egreso (gasto)")
        with st.form("form_egreso"):
            monto_egr = st.number_input("Monto S/.", min_value=0.01, step=0.01, format="%.2f", key="monto_egr")
            desc_egr = st.text_input("Descripción / motivo", key="desc_egr")
            cat_egr = st.selectbox("Categoría", [
                "Viáticos", "Transporte", "Materiales menores", "Limpieza/oficina", "Imprevistos", "Otros"
            ], key="cat_egr")
            comp_egr = st.file_uploader("Comprobante (foto/PDF)", type=["jpg", "png", "pdf"], key="comp_egr")

            if st.form_submit_button("Registrar Egreso", type="primary"):
                if monto_egr > 0:
                    ruta = guardar_comprobante(comp_egr, usuario) if comp_egr else ""
                    mov = {
                        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "usuario": usuario,
                        "tipo": "egreso",
                        "monto": monto_egr,
                        "descripcion": desc_egr,
                        "categoria": cat_egr,
                        "comprobante": ruta,
                        "estado": "Pendiente",
                        "aprobado_por": ""
                    }
                    guardar_movimiento(mov)
                    st.success("Egreso registrado. Espera aprobación del jefe.")
                    st.rerun()
                else:
                    st.error("El monto debe ser mayor a 0")

    with tab_mis:
        df = cargar_movimientos()
        mios = df[df["usuario"] == usuario]
        if mios.empty:
            st.info("No tienes movimientos registrados aún")
        else:
            st.dataframe(
                mios[["fecha", "tipo", "monto", "descripcion", "categoria", "estado"]].sort_values("fecha", ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={"monto": st.column_config.NumberColumn("Monto", format="S/. %.2f")}
            )

    with tab_apr:
        if not es_jefe:
            st.info("Solo el jefe puede aprobar movimientos")
            return

        df = cargar_movimientos()
        pendientes = df[(df["tipo"] == "egreso") & (df["estado"] == "Pendiente")]
        if pendientes.empty:
            st.success("No hay gastos pendientes de aprobación")
        else:
            for idx, row in pendientes.iterrows():
                with st.expander(f"{row['fecha']} | {row['usuario']} | S/ {row['monto']:.2f}"):
                    st.write("**Descripción:**", row["descripcion"])
                    st.write("**Categoría:**", row["categoria"])
                    if row["comprobante"]:
                        if row["comprobante"].lower().endswith((".jpg", ".png", ".jpeg")):
                            st.image(row["comprobante"], use_column_width=True)
                        else:
                            st.write("📄 Comprobante PDF adjunto")
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Aprobar", key=f"apr_{idx}"):
                            df.loc[idx, "estado"] = "Aprobado"
                            df.loc[idx, "aprobado_por"] = usuario
                            df.to_csv(DATA_FILE, index=False)
                            st.success("Aprobado")
                            st.rerun()
                    with col2:
                        if st.button("Rechazar", key=f"rec_{idx}"):
                            df.loc[idx, "estado"] = "Rechazado"
                            df.to_csv(DATA_FILE, index=False)
                            st.success("Rechazado")
                            st.rerun()
