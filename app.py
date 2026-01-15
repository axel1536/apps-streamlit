import streamlit as st
from datetime import date
import pandas as pd
import os
import unicodedata
from typing import Optional, Tuple, Dict, List, Any
from modules.caja_chica import mostrar_caja_chica

# ==================== CONFIGURACIÓN DE PÁGINA ====================
st.set_page_config(
    page_title="Control de Obras BOSS 2026",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== IMPORTS DE MÓDULOS ====================
from modules.logic import (
    guardar_fotos_avance,
    crear_avance_dict,
    preparar_historial_avances,
    validar_insumo,
    validar_obra,
    validar_insumo_duplicado,
    validar_costos_parte_diario,
    calcular_cantidad_hh,
    calcular_parcial,
    obtener_precio_insumo,
    validar_parte_diario_completo,
    calcular_totales_costos,
    calcular_resumen_presupuesto,
    calcular_eficiencia_rendimiento,
    obtener_estado_rendimiento,
    calcular_eficiencia_promedio_obra,
    validar_partida_cronograma,
    construir_curva_s_planificada,
    construir_curva_s_real,
    construir_tabla_curvas,
    calcular_resumen_cronograma,
    calcular_resumen_hitos,
    validar_hito_pago
)
from modules.database import (
    inicializar_directorios,
    cargar_obras,
    agregar_obra,
    agregar_avance,
    cargar_insumos,
    agregar_insumo,
    actualizar_insumo,
    eliminar_insumo,
    actualizar_presupuesto_obra,
    obtener_presupuesto_obra,
    obtener_avances_obra,
    obtener_cronograma_obra,
    agregar_partida_cronograma,
    actualizar_partida_cronograma,
    eliminar_partida_cronograma,
    obtener_hitos_pago_obra,
    agregar_hito_pago,
    actualizar_hito_pago,
    eliminar_hito_pago
)

# ==================== HELPERS ====================
def _norm_txt(s: str) -> str:
    s = str(s or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def _categoria_vacia(tipo: str) -> str:
    return f"⚠️ No hay insumos registrados en: {tipo}. Ve a 'Insumos y Materiales' y crea al menos uno."

def _map_freq(label: str) -> str:
    """Mapea etiquetas UI a códigos internos."""
    t = str(label or "").strip().lower()
    if t.startswith("d"):
        return "D"  # Diario
    if t.startswith("m"):
        return "M"  # Mensual
    return "W"      # Semanal (default)

def _freq_label(code: str) -> str:
    return {"D": "Diario", "W": "Semanal", "M": "Mensual"}.get(code, "Semanal")

def _parse_ts(x):
    try:
        return pd.to_datetime(x).normalize()
    except Exception:
        return None

def _autofreq_from_cronograma(items: list) -> str:
    """
    Elige automáticamente la vista:
    - <= 45 días: Diario
    - <= 210 días: Semanal
    - > 210 días: Mensual
    """
    if not items:
        return "W"

    starts, ends = [], []
    for it in items:
        s = _parse_ts(it.get("fecha_inicio"))
        e = _parse_ts(it.get("fecha_fin"))
        if s is not None and e is not None:
            starts.append(s)
            ends.append(e)

    if not starts or not ends:
        return "W"

    span_days = int((max(ends) - min(starts)).days) + 1
    if span_days <= 45:
        return "D"
    if span_days <= 210:
        return "W"
    return "M"

def _resample_sum(df: pd.DataFrame, freq_code: str) -> pd.DataFrame:
    """Re-muestrea sumando por periodo (manteniendo fecha como inicio del periodo)."""
    if df.empty:
        return df
    df = df.copy()
    df.index = pd.to_datetime(df.index).normalize()

    if freq_code == "D":
        return df

    if freq_code == "W":
        # Semana iniciando lunes (fecha = lunes)
        out = df.resample("W-MON", label="left", closed="left").sum()
        out.index = out.index.normalize()
        return out

    # Mensual (fecha = 1er día del mes)
    out = df.resample("MS").sum()
    out.index = out.index.normalize()
    return out

def _build_plan_df(crono_items: list, freq_code: str) -> pd.DataFrame:
    """
    Construye PV por periodo desde cronograma:
    distribuye monto_planificado uniforme entre días [inicio..fin].
    Retorna columns: fecha, plan_dia
    """
    if not crono_items:
        return pd.DataFrame(columns=["fecha", "plan_dia"])

    series = pd.Series(dtype="float64")

    for it in crono_items:
        s = _parse_ts(it.get("fecha_inicio"))
        e = _parse_ts(it.get("fecha_fin"))
        try:
            monto = float(it.get("monto_planificado", 0) or 0)
        except Exception:
            monto = 0.0

        if s is None or e is None or monto <= 0:
            continue
        if e < s:
            continue

        days = pd.date_range(s, e, freq="D")
        if len(days) == 0:
            continue

        daily = monto / len(days)
        s_part = pd.Series(daily, index=days)
        series = series.add(s_part, fill_value=0) if not series.empty else s_part

    if series.empty:
        return pd.DataFrame(columns=["fecha", "plan_dia"])

    df = series.to_frame("plan_dia")
    df.index.name = "fecha"
    df = _resample_sum(df, freq_code)
    return df.reset_index()

def _extract_total_from_avance(av: dict) -> float:
    """
    Intenta obtener el costo ejecutado del avance (AC) de forma robusta.
    Soporta distintos nombres de llave según tu modules.logic.
    """
    tot = av.get("totales")
    if isinstance(tot, dict):
        for k in ("total_ejecutado", "total_general_ejecutado", "total", "total_general", "total_costos"):
            v = tot.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        # fallback: suma valores numéricos del dict
        s = 0.0
        for v in tot.values():
            if isinstance(v, (int, float)):
                s += float(v)
        return float(s)

    # fallback si el avance trae un campo directo
    for k in ("total_ejecutado", "total_general_ejecutado", "total", "total_general", "monto", "costo"):
        v = av.get(k)
        if isinstance(v, (int, float)):
            return float(v)

    return 0.0

def _build_real_df(avances: list, freq_code: str) -> pd.DataFrame:
    """
    Construye AC por periodo desde avances (partes diarios).
    Retorna columns: fecha, real_dia
    """
    if not avances:
        return pd.DataFrame(columns=["fecha", "real_dia"])

    rows = []
    for av in avances:
        f = av.get("fecha") or av.get("Fecha") or av.get("date")
        ts = _parse_ts(f)
        if ts is None:
            continue
        total = _extract_total_from_avance(av)
        if total <= 0:
            continue
        rows.append((ts, float(total)))

    if not rows:
        return pd.DataFrame(columns=["fecha", "real_dia"])

    df = pd.DataFrame(rows, columns=["fecha", "real_dia"]).groupby("fecha", as_index=True).sum()
    df = _resample_sum(df, freq_code)
    df = df.reset_index()
    return df

def render_curva_s(cronograma_all: list, avances: list, rol: str = "jefe"):
    """
    Renderiza Curva S sin selector visible (vista automática).
    - JEFE: Plan = Aprobado; Real = partes diarios.
    - PASANTE: además muestra Plan (Pendiente - borrador) para que "vea algo" incluso si aún no aprueban.
    """
    cronograma_all = cronograma_all or []
    avances = avances or []

    # Normaliza defaults
    for it in cronograma_all:
        it.setdefault("estado", "Aprobado")
        it.setdefault("creado_por", "jefe")

    cron_aprob = [it for it in cronograma_all if it.get("estado") == "Aprobado"]
    cron_pend = [it for it in cronograma_all if it.get("estado") != "Aprobado"]

    # Vista automática (pero con opción avanzada opcional)
    freq_code = _autofreq_from_cronograma(cron_aprob or cron_pend)
    st.caption(f"Vista automática: {_freq_label(freq_code)}")

    with st.expander("Opciones avanzadas (opcional)", expanded=False):
        vista = st.radio(
            "Cambiar vista",
            ["Diario", "Semanal", "Mensual"],
            horizontal=True,
            index={"D": 0, "W": 1, "M": 2}.get(freq_code, 1)
        )
        freq_code = _map_freq(vista)

    # KPIs rápidos (para que sea obvio por qué no grafica)
    k1, k2, k3 = st.columns(3)
    with k1:
        st.metric("Partidas Aprobadas (Plan)", len(cron_aprob))
    with k2:
        st.metric("Partidas Pendientes", len(cron_pend))
    with k3:
        st.metric("Partes Diarios (Real)", len(avances))

    if not cron_aprob and cron_pend and rol == "jefe":
        st.warning("Hay partidas Pendientes. La Curva S del Plan solo considera partidas Aprobadas.")
    if not cron_aprob and cron_pend and rol == "pasante":
        st.info("Tus partidas están Pendientes. Se mostrará 'Plan (Pendiente - borrador)' hasta que el JEFE apruebe.")

    plan_df = _build_plan_df(cron_aprob, freq_code)
    real_df = _build_real_df(avances, freq_code)

    borr_df = pd.DataFrame(columns=["fecha", "plan_pend_dia"])
    if rol == "pasante" and cron_pend:
        tmp = _build_plan_df(cron_pend, freq_code)
        if not tmp.empty:
            borr_df = tmp.rename(columns={"plan_dia": "plan_pend_dia"})

    # Si no hay nada, informar claro
    has_any = (not plan_df.empty) or (not real_df.empty) or (rol == "pasante" and not borr_df.empty)
    if not has_any:
        st.info("No hay datos suficientes para graficar. Debes tener: (a) cronograma aprobado y/o (b) partes diarios con costos.")
        return

    # Merge y acumulados
    df = None
    if not plan_df.empty:
        df = plan_df.copy()
    else:
        df = pd.DataFrame(columns=["fecha", "plan_dia"])

    if not real_df.empty:
        df = pd.merge(df, real_df, on="fecha", how="outer")
    if rol == "pasante" and not borr_df.empty:
        df = pd.merge(df, borr_df, on="fecha", how="outer")

    df = df.fillna(0).sort_values("fecha")
    df = df.set_index("fecha")

    if "plan_dia" not in df.columns:
        df["plan_dia"] = 0.0
    if "real_dia" not in df.columns:
        df["real_dia"] = 0.0
    if rol == "pasante" and "plan_pend_dia" not in df.columns:
        df["plan_pend_dia"] = 0.0

    out = pd.DataFrame(index=df.index)
    out["Plan (Aprobado)"] = df["plan_dia"].cumsum()
    out["Real (Partes diarios)"] = df["real_dia"].cumsum()

    if rol == "pasante":
        out["Plan (Pendiente - borrador)"] = df["plan_pend_dia"].cumsum()

    st.line_chart(out, use_container_width=True)

    with st.expander("Ver detalle por periodo", expanded=False):
        det = df.copy()
        det["plan_acum"] = df["plan_dia"].cumsum()
        det["real_acum"] = df["real_dia"].cumsum()
        if rol == "pasante" and "plan_pend_dia" in df.columns:
            det["plan_pend_acum"] = df["plan_pend_dia"].cumsum()
        st.dataframe(det.reset_index(), use_container_width=True, hide_index=True)


# ==================== RESTRICCIÓN DE OBRAS POR PASANTE ====================
# Ajusta aquí si en tu empresa cambian los usuarios o nombres
PASANTE_OBRA_KEYWORDS = {
    # pasante-pachacutec => obra Ventanilla / Pachacutec
    "pasante-pachacutec": ["pachacutec", "ventanilla"],
    # pasante-rinconada => obra La Molina / Rinconada
    "pasante-rinconada": ["rinconada", "molina", "la molina"],
}

def obtener_obra_asignada_pasante(obras: dict, usuario: str):
    """
    Devuelve (codigo_obra, nombre_obra) asignado al pasante según su usuario.
    Busca por keywords en código o nombre (sin tildes, case-insensitive).
    """
    if not isinstance(usuario, str) or not usuario.startswith("pasante-"):
        return None, None

    kws = PASANTE_OBRA_KEYWORDS.get(usuario)
    if not kws:
        # Fallback: usa el tag del usuario pasante-<tag>
        kws = [usuario.split("pasante-", 1)[1]]

    kws = [_norm_txt(k) for k in kws if str(k).strip()]

    best_score = 0
    best_cod = None
    best_nom = None

    for cod, nom in (obras or {}).items():
        cod_n = _norm_txt(cod)
        nom_n = _norm_txt(nom)

        score = 0
        for kw in kws:
            if kw in cod_n:
                score += 3
            if kw in nom_n:
                score += 2

        if score > best_score:
            best_score = score
            best_cod = cod
            best_nom = nom

    if best_score <= 0:
        return None, None
    return best_cod, best_nom

# ==================== CONFIGURACIÓN INICIAL ====================
inicializar_directorios()

# ==================== AUTENTICACIÓN ====================
def check_password():
    def password_entered():
        users = st.secrets["users"]
        if (st.session_state.get("password") == users["jefe_pass"] and
                st.session_state.get("user") == users["jefe_user"]):
            st.session_state["auth"] = "jefe"
            st.session_state["usuario_logueado"] = st.session_state.get("user")
        elif (st.session_state.get("password") == users["pasante_pass"] and
              str(st.session_state.get("user", "")).startswith(users["pasante_user_prefix"])):
            st.session_state["auth"] = st.session_state.get("user")
            st.session_state["usuario_logueado"] = st.session_state.get("user")
        else:
            st.session_state["auth"] = False

    if "auth" not in st.session_state:
        col1, col2, col3 = st.columns([2, 1, 2])
        with col2:
            st.image("img/logo.png", width=300)

        col1, col2, col3 = st.columns([1, 4, 1])
        with col2:
            st.markdown("<h1 style='text-align:center;'>CONTROL DE OBRAS 2026</h1>", unsafe_allow_html=True)
            st.text_input("Usuario", key="user")
            st.text_input("Contraseña", type="password", key="password")
            st.button("INGRESAR", on_click=password_entered, use_container_width=True)
        return False

    if not st.session_state["auth"]:
        st.error("Usuario o contraseña incorrecta")
        return False
    return True

if not check_password():
    st.stop()

# ==================== INTERFAZ PRINCIPAL ====================
# ==================== MODO JEFE ====================
if st.session_state["auth"] == "jefe":
    with st.sidebar:
        st.image("img/logo.png", use_container_width=True)
        st.divider()

        obras = cargar_obras()

        if "obra_seleccionada" not in st.session_state:
            st.session_state.obra_seleccionada = None

        st.subheader("Seleccionar Obra")
        opciones_obras = ["-- Seleccionar --"] + [f"{nombre}" for codigo, nombre in obras.items()]
        codigos_obras = [None] + list(obras.keys())

        indice_actual = 0
        if st.session_state.obra_seleccionada:
            try:
                indice_actual = codigos_obras.index(st.session_state.obra_seleccionada)
            except ValueError:
                indice_actual = 0

        obra_seleccionada_idx = st.selectbox(
            "Obra:",
            range(len(opciones_obras)),
            format_func=lambda x: opciones_obras[x],
            index=indice_actual,
            key="selector_obra"
        )

        nuevo_codigo = codigos_obras[obra_seleccionada_idx]
        if nuevo_codigo != st.session_state.obra_seleccionada:
            st.session_state.obra_seleccionada = nuevo_codigo
            st.session_state.mostrar_form_obra = False
            st.session_state.mostrar_insumos = False
            st.rerun()

        if st.button("➕ Agregar Nueva Obra", key="agregar_obra_btn", use_container_width=True):
            st.session_state.mostrar_form_obra = True
            st.rerun()

        st.divider()

        if st.button("Insumos y Materiales", use_container_width=True):
            st.session_state.mostrar_insumos = True
            st.rerun()

        if st.sidebar.button("Caja Chica"):
            st.session_state["pagina"] = "caja"
            st.rerun()

    st.title("Modo Jefe de Obra")

    
    # ==================== SECCIÓN: INSUMOS Y MATERIALES ====================
    if "mostrar_insumos" in st.session_state and st.session_state.mostrar_insumos:
        st.header("Insumos y Materiales")

        if st.button("← Volver", use_container_width=False):
            st.session_state.mostrar_insumos = False
            st.rerun()

        insumos = cargar_insumos()

        st.subheader("Agregar Nuevo Insumo")

        if "form_insumo_counter" not in st.session_state:
            st.session_state.form_insumo_counter = 0

        if "indice_insumo_seleccionado" not in st.session_state:
            st.session_state.indice_insumo_seleccionado = 0

        with st.form(key=f"form_nuevo_insumo_{st.session_state.form_insumo_counter}"):
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                nombre_insumo = st.text_input("Nombre del Insumo", placeholder="ej: Cemento Portland")
            with col2:
                unidad_insumo = st.text_input("Unidad de Medida", placeholder="ej: KG, M3, UND, HH")
            with col3:
                precio_insumo = st.number_input("Precio Unitario (S/.)", min_value=0.0, step=0.01, format="%.2f")

            tipo_insumo = st.selectbox(
                "Tipo de Insumo",
                options=["materiales", "mano de obra", "equipos", "otros"],
                index=0
            )

            if st.form_submit_button("Agregar Insumo", use_container_width=True, type="primary"):
                es_valido, mensaje = validar_insumo(nombre_insumo, unidad_insumo, precio_insumo)

                if not es_valido:
                    st.error(f"❌ {mensaje}")
                elif validar_insumo_duplicado(nombre_insumo, insumos):
                    st.error(f"❌ Ya existe un insumo con el nombre '{nombre_insumo}'")
                else:
                    nuevo_insumo = {
                        "Insumo": nombre_insumo.strip(),
                        "Unidad": unidad_insumo.strip(),
                        "Precio Unitario": float(precio_insumo),
                        "Tipo": tipo_insumo
                    }
                    agregar_insumo(nuevo_insumo)
                    st.session_state.mensaje_insumo = "Insumo agregado correctamente"
                    st.session_state.form_insumo_counter += 1
                    st.rerun()

        if "mensaje_insumo" in st.session_state:
            st.success(st.session_state.mensaje_insumo)
            del st.session_state.mensaje_insumo

        if insumos:
            st.subheader("Listado de Insumos")
            df = pd.DataFrame(insumos)
            df.index = df.index + 1
            df.index.name = "ID"
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=False,
                column_config={
                    "Insumo": st.column_config.TextColumn("Insumo", width="large"),
                    "Unidad": st.column_config.TextColumn("Unidad", width="medium"),
                    "Precio Unitario": st.column_config.NumberColumn("Precio Unitario (S/.)", width="medium", format="S/. %.2f")
                }
            )

            st.subheader("Seleccionar Insumo")
            opciones = [f"{i}. {item['Insumo']} - S/. {item['Precio Unitario']}" for i, item in enumerate(insumos, 1)]

            def actualizar_indice_insumo():
                st.session_state.indice_insumo_seleccionado = opciones.index(st.session_state.selectbox_insumo)

            insumo_seleccionado = st.selectbox(
                "Selecciona un insumo para editar/eliminar:",
                opciones,
                index=st.session_state.indice_insumo_seleccionado,
                key="selectbox_insumo",
                on_change=actualizar_indice_insumo
            )

            indice_seleccionado = int(insumo_seleccionado.split(".")[0]) - 1
            insumo_actual = insumos[indice_seleccionado]
            st.info(f"**Insumo seleccionado:** {insumo_actual['Insumo']}")

            st.subheader("Editar")
            with st.form("form_editar_insumo"):
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    nuevo_nombre = st.text_input("Nombre del Insumo", value=insumo_actual["Insumo"])
                with col2:
                    nueva_unidad = st.text_input("Unidad de Medida", value=insumo_actual["Unidad"])
                with col3:
                    nuevo_precio = st.number_input(
                        "Precio Unitario (S/.)",
                        value=float(insumo_actual["Precio Unitario"]),
                        min_value=0.0,
                        step=0.01,
                        format="%.3f"
                    )

                tipo_actual = insumo_actual.get("Tipo", "otros")
                nuevo_tipo = st.selectbox(
                    "Tipo de Insumo",
                    options=["materiales", "mano de obra", "equipos", "otros"],
                    index=["materiales", "mano de obra", "equipos", "otros"].index(tipo_actual)
                )

                if st.form_submit_button("Guardar Cambios", use_container_width=True, type="primary"):
                    es_valido, mensaje = validar_insumo(nuevo_nombre, nueva_unidad, nuevo_precio)

                    if not es_valido:
                        st.error(f"❌ {mensaje}")
                    else:
                        nombre_cambio = nuevo_nombre.strip().lower() != insumo_actual["Insumo"].strip().lower()
                        otros_insumos = [ins for i, ins in enumerate(insumos) if i != indice_seleccionado]

                        if nombre_cambio and validar_insumo_duplicado(nuevo_nombre, otros_insumos):
                            st.error(f"❌ Ya existe un insumo con el nombre '{nuevo_nombre}'")
                        else:
                            insumo_actualizado = {
                                "Insumo": nuevo_nombre.strip(),
                                "Unidad": nueva_unidad.strip(),
                                "Precio Unitario": float(nuevo_precio),
                                "Tipo": nuevo_tipo
                            }
                            actualizar_insumo(indice_seleccionado, insumo_actualizado)
                            st.success("Insumo actualizado correctamente")
                            st.session_state.indice_insumo_seleccionado = 0
                            st.rerun()

            st.subheader("Eliminar")
            st.warning(f"**Se eliminará: {insumo_actual['Insumo']}**")
            if st.button("Eliminar insumo", use_container_width=True, type="secondary"):
                eliminar_insumo(indice_seleccionado)
                st.success("Insumo eliminado correctamente")
                st.session_state.indice_insumo_seleccionado = 0
                st.rerun()
        else:
            st.info("⚠️ No hay insumos registrados. Agrega uno en 'Agregar Insumo'.")

    # ==================== SECCIÓN: AGREGAR NUEVA OBRA ====================
    elif "mostrar_form_obra" in st.session_state and st.session_state.mostrar_form_obra:
        st.subheader("➕ Agregar Nueva Obra")

        with st.form("form_nueva_obra"):
            col1, col2 = st.columns(2)
            with col1:
                nuevo_codigo = st.text_input("Código de la Obra", placeholder="ej: obra2026")
            with col2:
                nuevo_nombre = st.text_input("Nombre de la Obra", placeholder="ej: Edificio Central – San Isidro")

            presupuesto_nuevo = st.number_input(
                "Presupuesto Total (S/.)",
                min_value=0.0,
                step=0.01,
                format="%.2f",
                help="Presupuesto inicial asignado a la obra",
            )

            if st.form_submit_button("Guardar", use_container_width=True):
                obras_actuales = cargar_obras()
                es_valido, mensaje = validar_obra(nuevo_codigo, nuevo_nombre, obras_actuales)

                if not es_valido:
                    st.error(f"❌ {mensaje}")
                else:
                    exito, mensaje_db = agregar_obra(nuevo_codigo, nuevo_nombre)
                    if exito:
                        ok_pres, msg_pres = actualizar_presupuesto_obra(nuevo_codigo, presupuesto_nuevo)
                        if not ok_pres:
                            st.warning(f"⚠️ Obra creada, pero no se pudo guardar el presupuesto: {msg_pres}")
                        st.success(f"Obra '{nuevo_nombre}' agregada exitosamente")
                        st.session_state.mostrar_form_obra = False
                        st.rerun()
                    else:
                        st.error(f"❌ {mensaje_db}")

        if st.button("Volver", use_container_width=True):
            st.session_state.mostrar_form_obra = False
            st.rerun()

    # ==================== SECCIÓN: VISTA DE OBRA SELECCIONADA ====================
    elif st.session_state.obra_seleccionada:
        obra_codigo = st.session_state.obra_seleccionada
        obra_nombre = obras.get(obra_codigo, "Obra no encontrada")

        st.header(f"{obra_nombre}")

        presupuesto = obtener_presupuesto_obra(obra_codigo)
        avances = obtener_avances_obra(obra_codigo)
        resumen = calcular_resumen_presupuesto(presupuesto, avances)

        st.markdown("### 💰 Resumen de Presupuesto")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Presupuestado", f"S/. {resumen['presupuestado']:,.2f}", help="Presupuesto total de la obra")

        with col2:
            st.metric(
                "Gastado",
                f"S/. {resumen['gastado']:,.2f}",
                delta=f"{resumen['porcentaje_gastado']:.1f}%",
                delta_color="inverse",
                help="Total acumulado de gastos ejecutados"
            )

        with col3:
            st.metric("Disponible", f"S/. {resumen['disponible']:,.2f}", help="Presupuesto restante")

        with col4:
            porcentaje = resumen['porcentaje_gastado']
            if porcentaje < 50:
                estado = "🟢 Saludable"
            elif porcentaje < 80:
                estado = "🟡 Moderado"
            elif porcentaje < 100:
                estado = "🟠 Crítico"
            else:
                estado = "🔴 Excedido"
            st.metric("Estado", estado, help="Estado del presupuesto según el porcentaje gastado")

        st.progress(min(resumen['porcentaje_gastado'] / 100, 1.0))

        st.divider()

        eficiencia_promedio = calcular_eficiencia_promedio_obra(avances)
        emoji_rendimiento, texto_rendimiento, _ = obtener_estado_rendimiento(eficiencia_promedio)

        st.markdown("### 📊 Rendimiento de Mano de Obra")
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Eficiencia Promedio", f"{eficiencia_promedio:.1f}%", help="Promedio de eficiencias de partes diarios")

        with col2:
            st.metric("Estado", f"{emoji_rendimiento} {texto_rendimiento}", help="Verde: ≥100% | Ámbar: 80-99% | Rojo: <80%")

        with col3:
            if eficiencia_promedio >= 100:
                st.metric("Superávit", f"+{(eficiencia_promedio - 100):.1f}%")
            else:
                st.metric("Déficit", f"-{(100 - eficiencia_promedio):.1f}%")

        with col4:
            if eficiencia_promedio >= 100:
                recomendacion = "✅ Mantener"
            elif eficiencia_promedio >= 80:
                recomendacion = "⚠️ Supervisar"
            else:
                recomendacion = "🚨 Evaluar"
            st.metric("Acción", recomendacion)

        if eficiencia_promedio > 0:
            st.progress(min(eficiencia_promedio / 100, 1.0))

        st.divider()

        tab1, tab2, tab3, tab4 = st.tabs(["Parte Diario", "Historial de Avances", "Cronograma Valorizado", "Próximamente"])

        st.divider()
    
       # ==================== Caja Chica ====================
        if st.session_state.get("pagina") == "caja":
            st.header("Caja Chica")
            if st.button("← Volver al menú principal"):
                st.session_state["pagina"] = None
                st.rerun()
    
            mostrar_caja_chica()   # ← tu función
            st.stop()

        # ==================== TAB 1: PARTE DIARIO (JEFE) ====================
        with tab1:
            st.subheader("Parte Diario del Día")
            hoy = date.today()

            if "form_parte_diario_counter" not in st.session_state:
                st.session_state.form_parte_diario_counter = 0

            if "insumos_mo_confirmados" not in st.session_state:
                st.session_state.insumos_mo_confirmados = []
            if "insumos_mat_confirmados" not in st.session_state:
                st.session_state.insumos_mat_confirmados = []
            if "insumos_eq_confirmados" not in st.session_state:
                st.session_state.insumos_eq_confirmados = []
            if "insumos_otros_confirmados" not in st.session_state:
                st.session_state.insumos_otros_confirmados = []

            counter = st.session_state.form_parte_diario_counter

            st.markdown("### Información General")
            col1, col2 = st.columns(2)
            with col1:
                nombre_default = st.session_state.get("usuario_logueado", "Usuario")
                responsable = st.text_input("Tu nombre", value=nombre_default, key=f"responsable_input_{counter}")
            with col2:
                avance = st.slider("Avance logrado hoy (%)", 0, 30, 5, key=f"avance_input_{counter}")

            col1, col2 = st.columns(2)
            with col1:
                name_partida = st.text_input(
                    "Nombre de la partida o actividad realizada hoy",
                    placeholder="ej: Cimentación, Estructura, Albañilería, etc.",
                    key=f"name_partida_input_{counter}"
                )
            with col2:
                col1b, col2b = st.columns(2)
                with col1b:
                    cantidad_ejecutada = st.number_input(
                        "Metrado Ejecutado",
                        min_value=0.0,
                        step=0.1,
                        placeholder="Ingresa la cantidad realizada",
                        key=f"cantidad_ejecutada_{counter}"
                    )
                with col2b:
                    unidad_medida = st.text_input(
                        "Unidad",
                        placeholder="ej: M3, KG, UND, HH",
                        key=f"unidad_input_{counter}"
                    )

            col1, col2 = st.columns(2)
            with col1:
                horas_mano_obra = st.number_input("Jornada Laboral (h)", min_value=0, step=1, value=8, key=f"horas_input_{counter}")
            with col2:
                rendimiento_partida = st.number_input(
                    "Rendimiento Esperado de la Partida (por día)",
                    min_value=0.0,
                    step=0.1,
                    value=6.0,
                    help="Rendimiento en unidad/día. Se ajusta proporcionalmente si la jornada no es de 8 horas.",
                    key=f"rendimiento_input_{counter}"
                )

            st.markdown("### Costos")
            insumos_lista = cargar_insumos()

            tab_mo, tab_mat, tab_eq, tab_otros = st.tabs(["Mano de Obra", "Materiales", "Equipos", "Otros"])

            with tab_mo:
                insumos_mo = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "mano de obra"]
                if not insumos_mo:
                    st.info(_categoria_vacia("Mano de Obra"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns([1.5, 0.5, 0.5])
                    with col1:
                        desc_mano_obra = st.selectbox("Descripción", options=insumos_mo, key="desc_mo")
                    with col2:
                        cant_mano_obra = st.number_input(
                            "Cuadrilla",
                            min_value=0.0,
                            step=0.1,
                            value=1.0,
                            format="%.2f",
                            key="cant_mo",
                            help="Proporción: ej. 0.1, 0.5, 1.0"
                        )
                    with col3:
                        precio_mano_obra = obtener_precio_insumo(insumos_lista, desc_mano_obra)
                        st.metric("Precio Unitario", f"S/. {precio_mano_obra:.2f}")

                    if st.button("Confirmar Mano de Obra", use_container_width=True, type="primary", key="btn_confirmar_mo"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_mano_obra <= 0:
                            st.error("❌ La cuadrilla debe ser mayor a 0")
                        else:
                            cantidad_hh = calcular_cantidad_hh(cant_mano_obra, horas_mano_obra, rendimiento_partida)
                            parcial_mo = calcular_parcial(cantidad_hh, precio_mano_obra)
                            item = {
                                "Descripción": desc_mano_obra,
                                "Cuadrilla": cant_mano_obra,
                                "Precio Unit.": precio_mano_obra,
                                "Cantidad (HH)": cantidad_hh,
                                "Parcial (S/)": parcial_mo
                            }
                            st.session_state.insumos_mo_confirmados.append(item)
                            st.success(f"✓ {desc_mano_obra} agregado")
                            st.rerun()

            with tab_mat:
                insumos_mat = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "materiales"]
                if not insumos_mat:
                    st.info(_categoria_vacia("Materiales"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_materiales = st.selectbox("Descripción", options=insumos_mat, key="desc_mat")
                    with col2:
                        cant_materiales = st.number_input("Cantidad", min_value=0.0000, step=0.0010, format="%.4f", key="cant_mat")
                    with col3:
                        precio_materiales = obtener_precio_insumo(insumos_lista, desc_materiales)
                        st.metric("Precio Unitario", f"S/. {precio_materiales:.2f}")

                    if st.button("Confirmar Material", use_container_width=True, type="primary", key="btn_confirmar_mat"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_materiales <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_mat = calcular_parcial(cant_materiales, precio_materiales)
                            item = {
                                "Descripción": desc_materiales,
                                "Cantidad": cant_materiales,
                                "Precio Unit.": precio_materiales,
                                "Parcial (S/)": parcial_mat
                            }
                            st.session_state.insumos_mat_confirmados.append(item)
                            st.success(f"✓ {desc_materiales} agregado")
                            st.rerun()

            with tab_eq:
                insumos_eq = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "equipos"]
                if not insumos_eq:
                    st.info(_categoria_vacia("Equipos"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_equipos = st.selectbox("Descripción", options=insumos_eq, key="desc_eq")
                    with col2:
                        cant_equipos = st.number_input("Cantidad", min_value=0.0, step=1.0, key="cant_eq")
                    with col3:
                        precio_equipos = obtener_precio_insumo(insumos_lista, desc_equipos)
                        st.metric("Precio Unitario", f"S/. {precio_equipos:.2f}")

                    if st.button("Confirmar Equipo", use_container_width=True, type="primary", key="btn_confirmar_eq"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_equipos <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_eq = calcular_parcial(cant_equipos, precio_equipos)
                            item = {
                                "Descripción": desc_equipos,
                                "Cantidad": cant_equipos,
                                "Precio Unit.": precio_equipos,
                                "Parcial (S/)": parcial_eq
                            }
                            st.session_state.insumos_eq_confirmados.append(item)
                            st.success(f"✓ {desc_equipos} agregado")
                            st.rerun()

            with tab_otros:
                insumos_otros = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "otros"]
                if not insumos_otros:
                    st.info(_categoria_vacia("Otros"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_otros = st.selectbox("Descripción", options=insumos_otros, key="desc_otros")
                    with col2:
                        cant_otros = st.number_input("Cantidad", min_value=0.0, step=0.01, key="cant_otros")
                    with col3:
                        precio_otros = obtener_precio_insumo(insumos_lista, desc_otros)
                        st.metric("Precio Unitario", f"S/. {precio_otros:.2f}")

                    if st.button("Confirmar Otro", use_container_width=True, type="primary", key="btn_confirmar_otros"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_otros <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_otros = calcular_parcial(cant_otros, precio_otros)
                            item = {
                                "Descripción": desc_otros,
                                "Cantidad": cant_otros,
                                "Precio Unit.": precio_otros,
                                "Parcial (S/)": parcial_otros
                            }
                            st.session_state.insumos_otros_confirmados.append(item)
                            st.success(f"✓ {desc_otros} agregado")
                            st.rerun()

            # Listas confirmadas
            if st.session_state.insumos_mo_confirmados:
                st.markdown("#### Mano de Obra Confirmada")
                st.dataframe(pd.DataFrame(st.session_state.insumos_mo_confirmados), use_container_width=True, hide_index=True)
                if st.button("🗑️ Limpiar Mano de Obra", key="limpiar_mo"):
                    st.session_state.insumos_mo_confirmados = []
                    st.rerun()

            if st.session_state.insumos_mat_confirmados:
                st.markdown("#### Materiales Confirmados")
                st.dataframe(pd.DataFrame(st.session_state.insumos_mat_confirmados), use_container_width=True, hide_index=True)
                if st.button("🗑️ Limpiar Materiales", key="limpiar_mat"):
                    st.session_state.insumos_mat_confirmados = []
                    st.rerun()

            if st.session_state.insumos_eq_confirmados:
                st.markdown("#### Equipos Confirmados")
                st.dataframe(pd.DataFrame(st.session_state.insumos_eq_confirmados), use_container_width=True, hide_index=True)
                if st.button("🗑️ Limpiar Equipos", key="limpiar_eq"):
                    st.session_state.insumos_eq_confirmados = []
                    st.rerun()

            if st.session_state.insumos_otros_confirmados:
                st.markdown("#### Otros Confirmados")
                st.dataframe(pd.DataFrame(st.session_state.insumos_otros_confirmados), use_container_width=True, hide_index=True)
                if st.button("🗑️ Limpiar Otros", key="limpiar_otros"):
                    st.session_state.insumos_otros_confirmados = []
                    st.rerun()

            st.markdown("### 📊 Resumen de Costos Consolidado")
            total_mo = sum([item["Parcial (S/)"] for item in st.session_state.insumos_mo_confirmados])
            total_mat = sum([item["Parcial (S/)"] for item in st.session_state.insumos_mat_confirmados])
            total_eq = sum([item["Parcial (S/)"] for item in st.session_state.insumos_eq_confirmados])
            total_otros = sum([item["Parcial (S/)"] for item in st.session_state.insumos_otros_confirmados])
            total_general = total_mo + total_mat + total_eq + total_otros
            total_general_ejecutado = total_general * cantidad_ejecutada if cantidad_ejecutada > 0 else 0

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Mano de Obra", f"S/. {total_mo:.2f}")
            with col2:
                st.metric("Materiales", f"S/. {total_mat:.2f}")
            with col3:
                st.metric("Equipos", f"S/. {total_eq:.2f}")
            with col4:
                st.metric("Otros", f"S/. {total_otros:.2f}")

            col1, col2 = st.columns(2)
            with col1:
                unidad_label = unidad_medida if unidad_medida.strip() else "unidad"
                st.metric(f"TOTAL/{unidad_label}", f"S/. {total_general:.2f}")
            with col2:
                st.metric("TOTAL EJECUTADO", f"S/. {total_general_ejecutado:.2f}", delta_color="off")

            st.markdown("### Finalizar Parte Diario")
            obs = st.text_area("Observaciones", key=f"obs_final_{counter}")
            fotos = st.file_uploader("Fotos del avance", accept_multiple_files=True, type=["jpg", "png", "jpeg"], key=f"fotos_final_{counter}")

            st.session_state["cantidad_ejecutada_cache"] = cantidad_ejecutada
            st.session_state["unidad_medida_cache"] = unidad_medida

            if 0 < len(fotos) < 3:
                st.warning("⚠️ Debes subir mínimo 3 fotos")

            @st.dialog("Confirmar Envío de Parte Diario")
            def confirmar_envio_modal():
                st.warning("⚠️ ¿Estás seguro de enviar el parte diario?")
                st.write("Esta acción guardará el registro y limpiará todos los campos.")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ SÍ, ENVIAR", use_container_width=True, type="primary"):
                        cantidad_ejecutada_cache = st.session_state.get("cantidad_ejecutada_cache", 0)

                        totales = calcular_totales_costos(
                            st.session_state.insumos_mo_confirmados,
                            st.session_state.insumos_mat_confirmados,
                            st.session_state.insumos_eq_confirmados,
                            st.session_state.insumos_otros_confirmados,
                            cantidad_ejecutada=cantidad_ejecutada_cache if cantidad_ejecutada_cache > 0 else 1
                        )

                        rutas_fotos = guardar_fotos_avance(obra_codigo, fotos, hoy)

                        nuevo_avance = crear_avance_dict(
                            fecha=hoy,
                            responsable=responsable,
                            avance_pct=avance,
                            observaciones=obs,
                            rutas_fotos=rutas_fotos,
                            nombre_partida=name_partida,
                            rendimiento_partida=rendimiento_partida,
                            unidad_medida=st.session_state.get("unidad_medida_cache", unidad_medida),
                            horas_mano_obra=horas_mano_obra,
                            cantidad_ejecutada=cantidad_ejecutada_cache,
                            insumos_mo=st.session_state.insumos_mo_confirmados,
                            insumos_mat=st.session_state.insumos_mat_confirmados,
                            insumos_eq=st.session_state.insumos_eq_confirmados,
                            insumos_otros=st.session_state.insumos_otros_confirmados,
                            totales=totales
                        )

                        exito, mensaje_db = agregar_avance(obra_codigo, nuevo_avance)
                        if not exito:
                            st.error(f"❌ Error al guardar: {mensaje_db}")
                            return

                        st.session_state.insumos_mo_confirmados = []
                        st.session_state.insumos_mat_confirmados = []
                        st.session_state.insumos_eq_confirmados = []
                        st.session_state.insumos_otros_confirmados = []

                        st.session_state.form_parte_diario_counter += 1
                        st.success("✅ ¡Parte diario enviado correctamente!")
                        st.balloons()
                        st.rerun()

                with c2:
                    if st.button("❌ CANCELAR", use_container_width=True, type="secondary"):
                        st.rerun()

            if st.button("📤 ENVIAR PARTE DIARIO", use_container_width=True, type="primary", key="enviar_final"):
                es_valido, errores = validar_parte_diario_completo(responsable, avance, rendimiento_partida, unidad_medida, fotos)
                costos_validos, mensaje_costos = validar_costos_parte_diario(
                    st.session_state.insumos_mo_confirmados,
                    st.session_state.insumos_mat_confirmados,
                    st.session_state.insumos_eq_confirmados,
                    st.session_state.insumos_otros_confirmados
                )

                if not es_valido:
                    for error in errores:
                        st.error(f"❌ {error}")
                elif not costos_validos:
                    st.error(f"❌ {mensaje_costos}")
                else:
                    confirmar_envio_modal()

        # ==================== TAB 2: HISTORIAL DE AVANCES (JEFE) ====================
        with tab2:
            st.subheader("Historial de Avances")
            historial = preparar_historial_avances(obra_codigo)

            if historial:
                for item in historial:
                    with st.expander(f"📅 {item['fecha_fmt']} - {item['responsable']} ({item['avance_pct']}%)"):
                        st.markdown("### Información General")
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            st.write("**Responsable:**", item["responsable"])
                            st.write("**Avance del día:**", f"{item['avance_pct']}%")
                        with c2:
                            partida = item.get("partida", {})
                            if isinstance(partida, dict):
                                st.write(f"**Partida:** {partida.get('nombre', 'N/A')}")
                                st.write(f"**Rendimiento:** {partida.get('rendimiento', 0):.2f} {partida.get('unidad', '')}/día")
                        with c3:
                            partida = item.get("partida", {})
                            if isinstance(partida, dict):
                                st.write(f"**Cantidad Ejecutada:** {partida.get('cantidad_ejecutada', 0):.2f} {partida.get('unidad', '')}")
                                st.write(f"**Jornal:** {partida.get('jornal_horas', 0)} horas")

                        partida = item.get("partida", {})
                        if isinstance(partida, dict):
                            cant = partida.get("cantidad_ejecutada", 0)
                            rend = partida.get("rendimiento", 0)
                            hrs = partida.get("jornal_horas", 0)
                            if rend > 0 and hrs > 0 and cant > 0:
                                eff = calcular_eficiencia_rendimiento(cant, rend, hrs)
                                emoji, texto, _ = obtener_estado_rendimiento(eff)
                                st.markdown("---")
                                st.markdown(f"### {emoji} Eficiencia de Rendimiento: {eff:.1f}%")

                        costos = item.get("costos", {})
                        totales = item.get("totales", {})

                        if costos and any(costos.values()):
                            st.markdown("### Costos del Día")
                            if costos.get("mano_de_obra"):
                                st.markdown("#### Mano de Obra")
                                st.dataframe(pd.DataFrame(costos["mano_de_obra"]), use_container_width=True, hide_index=True)
                            if costos.get("materiales"):
                                st.markdown("#### Materiales")
                                st.dataframe(pd.DataFrame(costos["materiales"]), use_container_width=True, hide_index=True)
                            if costos.get("equipos"):
                                st.markdown("#### Equipos")
                                st.dataframe(pd.DataFrame(costos["equipos"]), use_container_width=True, hide_index=True)
                            if costos.get("otros"):
                                st.markdown("#### Otros")
                                st.dataframe(pd.DataFrame(costos["otros"]), use_container_width=True, hide_index=True)

                            if totales:
                                st.markdown("#### Resumen de Totales")
                                c1, c2, c3, c4 = st.columns(4)
                                with c1:
                                    st.metric("Mano de Obra", f"S/. {totales.get('mano_de_obra', 0):.2f}")
                                with c2:
                                    st.metric("Materiales", f"S/. {totales.get('materiales', 0):.2f}")
                                with c3:
                                    st.metric("Equipos", f"S/. {totales.get('equipos', 0):.2f}")
                                with c4:
                                    st.metric("Otros", f"S/. {totales.get('otros', 0):.2f}")

                        if item.get("obs"):
                            st.markdown("### 📝 Observaciones")
                            st.write(item["obs"])

                        if item.get("fotos"):
                            st.markdown("### 📷 Fotos del avance")
                            cols = st.columns(min(len(item["fotos"]), 3))
                            for i, foto_path in enumerate(item["fotos"]):
                                target = cols[i % 3]
                                if foto_path and os.path.exists(foto_path):
                                    target.image(foto_path, caption=os.path.basename(foto_path))
                                else:
                                    target.warning(f"No se encontró la imagen: {os.path.basename(foto_path) if foto_path else 'Archivo no especificado'}")
            else:
                st.info("No hay partes diarios registrados para esta obra aún.")

        # ==================== TAB 3: CRONOGRAMA VALORIZADO (JEFE) ====================
        with tab3:
            st.markdown("## 📊 Cronograma Valorizado y Control de Avance")
            st.caption("Gestiona el cronograma de la obra, visualiza la Curva S y controla los hitos de pago")

            # Cargar datos
            cronograma_all = obtener_cronograma_obra(obra_codigo) or []
            hitos = obtener_hitos_pago_obra(obra_codigo) or []

            for it in cronograma_all:
                it.setdefault("estado", "Aprobado")
                it.setdefault("creado_por", "jefe")
            for h in hitos:
                h.setdefault("estado", "Pendiente")
                h.setdefault("creado_por", "jefe")

            cronograma_aprob = [it for it in cronograma_all if it.get("estado") == "Aprobado"]
            resumen_crono = calcular_resumen_cronograma(cronograma_aprob, avances)

            # ========== RESUMEN EJECUTIVO ==========
            st.markdown("### 📈 Resumen Ejecutivo del Proyecto")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(
                    "💰 Plan Total",
                    f"S/. {resumen_crono['pv_total']:,.2f}",
                    help="Monto total planificado del cronograma"
                )
            with col2:
                delta_ac = resumen_crono['ac_total'] - resumen_crono['pv_total']
                st.metric(
                    "💵 Real Ejecutado",
                    f"S/. {resumen_crono['ac_total']:,.2f}",
                    delta=f"S/. {delta_ac:,.2f}",
                    delta_color="inverse" if delta_ac > 0 else "normal",
                    help="Monto total ejecutado hasta la fecha"
                )
            with col3:
                porc_avance = (resumen_crono['ac_total'] / resumen_crono['pv_total'] * 100) if resumen_crono['pv_total'] > 0 else 0
                st.metric(
                    "📊 Avance Físico",
                    f"{porc_avance:.1f}%",
                    help="Porcentaje de avance del proyecto"
                )
            with col4:
                spi = resumen_crono.get("spi", 0)
                if spi == 0:
                    estado_txt = "⚪ Sin datos"
                    estado_color = "off"
                elif spi <= 0.95:
                    estado_txt = "🟢 En control"
                    estado_color = "normal"
                elif spi <= 1.05:
                    estado_txt = "🟡 Atención"
                    estado_color = "off"
                else:
                    estado_txt = "🔴 Sobrecosto"
                    estado_color = "inverse"
                
                st.metric(
                    "Estado del Proyecto",
                    estado_txt,
                    delta=f"Índice: {spi:.2f}" if spi > 0 else None,
                    delta_color=estado_color,
                    help="Verde: ≤0.95 | Amarillo: 0.96-1.05 | Rojo: >1.05"
                )

            # Barra de progreso
            if resumen_crono['pv_total'] > 0:
                progress_val = min(resumen_crono['ac_total'] / resumen_crono['pv_total'], 1.0)
                st.progress(progress_val)
                st.caption(f"Avance: S/. {resumen_crono['ac_total']:,.2f} de S/. {resumen_crono['pv_total']:,.2f}")

            st.divider()

            # ========== PESTAÑAS PARA ORGANIZAR CONTENIDO ==========
            tab_partidas, tab_curva, tab_hitos = st.tabs([
                "📋 Partidas del Cronograma",
                "📈 Curva S (Plan vs Real)",
                "💰 Hitos de Pago"
            ])

            # ===== TAB: PARTIDAS =====
            with tab_partidas:
                st.markdown("### Gestión de Partidas del Cronograma")
                
                # Mostrar solicitudes pendientes si las hay
                pendientes = [p for p in cronograma_all if p.get("estado") == "Pendiente"]
                if pendientes:
                    st.warning(f"⚠️ Tienes **{len(pendientes)}** solicitud(es) pendiente(s) de aprobación del pasante")
                    
                    with st.expander(f"🔔 Ver {len(pendientes)} Solicitud(es) Pendiente(s)", expanded=True):
                        for i, pend in enumerate(pendientes):
                            col1, col2, col3 = st.columns([3, 1, 1])
                            with col1:
                                st.markdown(f"**{pend.get('nombre', 'Sin nombre')}**")
                                st.caption(f"📅 {pend.get('fecha_inicio')} → {pend.get('fecha_fin')} | S/. {float(pend.get('monto_planificado', 0)):,.2f}")
                                if pend.get('descripcion'):
                                    st.caption(f"📝 {pend.get('descripcion')}")
                                st.caption(f"👤 Solicitado por: {pend.get('creado_por', 'desconocido')}")
                            with col2:
                                if st.button("✅ Aprobar", key=f"aprobar_pend_{i}", use_container_width=True, type="primary"):
                                    payload = dict(pend)
                                    payload["estado"] = "Aprobado"
                                    ok, msg = actualizar_partida_cronograma(obra_codigo, pend.get("id"), payload)
                                    if ok:
                                        st.success("✅ Partida aprobada")
                                        st.rerun()
                                    else:
                                        st.error(f"❌ {msg}")
                            with col3:
                                if st.button("❌ Rechazar", key=f"rechazar_pend_{i}", use_container_width=True):
                                    ok, msg = eliminar_partida_cronograma(obra_codigo, pend.get("id"))
                                    if ok:
                                        st.success("✅ Solicitud rechazada")
                                        st.rerun()
                                    else:
                                        st.error(f"❌ {msg}")
                            st.divider()

                st.markdown("#### ➕ Agregar Nueva Partida")
                
                if "form_crono_counter" not in st.session_state:
                    st.session_state.form_crono_counter = 0

                with st.form(key=f"form_add_crono_{st.session_state.form_crono_counter}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        crono_nombre = st.text_input("📋 Nombre de la Partida*", placeholder="Ej: Cimentación, Acabados, Instalaciones Eléctricas")
                    with c2:
                        crono_monto = st.number_input("💵 Monto Planificado (S/.)*", min_value=0.0, step=100.0, format="%.2f")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        crono_inicio = st.date_input("📅 Fecha de Inicio*", value=date.today())
                    with c2:
                        crono_fin = st.date_input("📅 Fecha de Fin*", value=date.today())

                    crono_desc = st.text_area("📝 Descripción (opcional)", placeholder="Detalles adicionales sobre la partida...", height=80)

                    if st.form_submit_button("✅ Agregar Partida (Aprobado)", use_container_width=True, type="primary"):
                        ok, msg = validar_partida_cronograma(crono_nombre, crono_inicio, crono_fin, crono_monto)
                        if not ok:
                            st.error(f"❌ {msg}")
                        else:
                            partida = {
                                "nombre": crono_nombre.strip(),
                                "fecha_inicio": str(crono_inicio),
                                "fecha_fin": str(crono_fin),
                                "monto_planificado": float(crono_monto),
                                "descripcion": crono_desc.strip(),
                                "estado": "Aprobado",
                                "creado_por": st.session_state.get("usuario_logueado", "jefe"),
                            }
                            ok2, msg2 = agregar_partida_cronograma(obra_codigo, partida)
                            if ok2:
                                st.success("✅ Partida agregada correctamente")
                                st.session_state.form_crono_counter += 1
                                st.rerun()
                            else:
                                st.error(f"❌ {msg2}")

                st.divider()

                # Lista de partidas
                cronograma_all = obtener_cronograma_obra(obra_codigo) or []
                for it in cronograma_all:
                    it.setdefault("estado", "Aprobado")
                    it.setdefault("creado_por", "jefe")

                if not cronograma_all:
                    st.info("📭 No hay partidas registradas. Agrega la primera partida para comenzar tu cronograma.")
                else:
                    st.markdown(f"#### 📊 Listado de Partidas ({len(cronograma_all)} total)")
                    
                    # Filtros
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        filtro_estado = st.selectbox(
                            "Filtrar por estado:",
                            ["Todos", "Aprobado", "Pendiente"],
                            key="filtro_estado_partidas"
                        )
                    
                    partidas_filtradas = cronograma_all if filtro_estado == "Todos" else [p for p in cronograma_all if p.get("estado") == filtro_estado]
                    
                    # Tabla de partidas
                    dfc = pd.DataFrame(partidas_filtradas)
                    if not dfc.empty:
                        cols_mostrar = ["nombre", "fecha_inicio", "fecha_fin", "monto_planificado", "estado", "creado_por"]
                        cols_existentes = [c for c in cols_mostrar if c in dfc.columns]
                        
                        st.dataframe(
                            dfc[cols_existentes].rename(columns={
                                "nombre": "Partida",
                                "fecha_inicio": "Inicio",
                                "fecha_fin": "Fin",
                                "monto_planificado": "Monto (S/.)",
                                "estado": "Estado",
                                "creado_por": "Creado por",
                            }),
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Monto (S/.)": st.column_config.NumberColumn(format="S/. %.2f")
                            }
                        )
                    
                    st.markdown("#### ✏️ Editar / Eliminar Partida")
                    
                    if "idx_partida_crono" not in st.session_state:
                        st.session_state.idx_partida_crono = 0

                    opciones = [
                        f"{it.get('nombre', '(sin nombre)')} - S/. {float(it.get('monto_planificado', 0) or 0):,.2f} [{it.get('estado', 'Aprobado')}]"
                        for it in cronograma_all
                    ]

                    sel = st.selectbox(
                        "Selecciona una partida para editar:",
                        opciones,
                        index=min(st.session_state.idx_partida_crono, len(opciones) - 1),
                        key="sel_partida_crono"
                    )
                    st.session_state.idx_partida_crono = opciones.index(sel)

                    partida_sel = cronograma_all[st.session_state.idx_partida_crono]
                    partida_id = partida_sel.get("id")

                    with st.form("form_edit_crono"):
                        c1, c2 = st.columns(2)
                        with c1:
                            n_nombre = st.text_input("Nombre", value=partida_sel.get("nombre", ""))
                        with c2:
                            n_monto = st.number_input(
                                "Monto (S/.)",
                                min_value=0.0,
                                step=100.0,
                                format="%.2f",
                                value=float(partida_sel.get("monto_planificado", 0.0) or 0.0)
                            )
                        
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            n_inicio = st.date_input("Inicio", value=pd.to_datetime(partida_sel.get("fecha_inicio", date.today())).date())
                        with c2:
                            n_fin = st.date_input("Fin", value=pd.to_datetime(partida_sel.get("fecha_fin", date.today())).date())
                        with c3:
                            n_estado = st.selectbox(
                                "Estado",
                                ["Pendiente", "Aprobado"],
                                index=0 if partida_sel.get("estado") == "Pendiente" else 1
                            )

                        n_desc = st.text_area("Descripción", value=partida_sel.get("descripcion", ""), height=80)

                        colx, coly = st.columns(2)
                        with colx:
                            guardar = st.form_submit_button("💾 Guardar Cambios", use_container_width=True, type="primary")
                        with coly:
                            eliminar = st.form_submit_button("🗑️ Eliminar Partida", use_container_width=True)

                    if guardar:
                        ok, msg = validar_partida_cronograma(n_nombre, n_inicio, n_fin, n_monto)
                        if not ok:
                            st.error(f"❌ {msg}")
                        else:
                            payload = {
                                "nombre": n_nombre.strip(),
                                "fecha_inicio": str(n_inicio),
                                "fecha_fin": str(n_fin),
                                "monto_planificado": float(n_monto),
                                "descripcion": n_desc.strip(),
                                "estado": n_estado,
                                "creado_por": partida_sel.get("creado_por", "jefe"),
                            }
                            ok2, msg2 = actualizar_partida_cronograma(obra_codigo, partida_id, payload)
                            if ok2:
                                st.success("✅ Partida actualizada correctamente")
                                st.rerun()
                            else:
                                st.error(f"❌ {msg2}")

                    if eliminar:
                        ok3, msg3 = eliminar_partida_cronograma(obra_codigo, partida_id)
                        if ok3:
                            st.success("✅ Partida eliminada correctamente")
                            st.session_state.idx_partida_crono = 0
                            st.rerun()
                        else:
                            st.error(f"❌ {msg3}")

            # ===== TAB: CURVA S =====
            with tab_curva:
                st.markdown("### 📈 Curva S - Análisis de Avance del Proyecto")
                st.caption("Compara el avance planificado vs real. Solo se consideran partidas **Aprobadas** en el plan.")
                
                freq = st.selectbox(
                    "🗓️ Agrupar datos por:",
                    ["Diario", "Semanal", "Mensual"],
                    index=1,
                    key="freq_curva_s",
                    help="Selecciona la frecuencia de agrupación de datos"
                )
                
                cronograma_aprob = [it for it in (obtener_cronograma_obra(obra_codigo) or []) if it.get("estado", "Aprobado") == "Aprobado"]

                plan_df = construir_curva_s_planificada(cronograma_aprob, freq=freq)
                real_df = construir_curva_s_real(avances, freq=freq)

                if plan_df.empty and real_df.empty:
                    st.info("📊 No hay datos suficientes para graficar la Curva S. Asegúrate de tener:")
                    st.markdown("- ✅ Partidas aprobadas en el cronograma")
                    st.markdown("- ✅ Partes diarios registrados con costos")
                else:
                    df_plot = pd.merge(plan_df, real_df, on="fecha", how="outer").fillna(0).sort_values("fecha")
                    df_plot = df_plot.set_index("fecha")

                    if "plan_acum" not in df_plot.columns:
                        df_plot["plan_acum"] = df_plot.get("plan_dia", 0).cumsum()
                    if "real_acum" not in df_plot.columns:
                        df_plot["real_acum"] = df_plot.get("real_dia", 0).cumsum()

                    # Métricas de análisis
                    st.markdown("#### 📊 Indicadores Clave")
                    col1, col2, col3, col4 = st.columns(4)
                    
                    plan_actual = resumen_crono.get('pv_to_date', 0)
                    real_actual = resumen_crono.get('ac_to_date', 0)
                    variacion = resumen_crono.get('sv', 0)
                    spi = resumen_crono.get('spi', 0)
                    
                    with col1:
                        st.metric(
                            "📅 Plan a la Fecha",
                            f"S/. {plan_actual:,.2f}",
                            help="Monto que debería estar gastado según el cronograma"
                        )
                    with col2:
                        st.metric(
                            "💵 Real a la Fecha",
                            f"S/. {real_actual:,.2f}",
                            delta=f"S/. {real_actual - plan_actual:,.2f}",
                            delta_color="inverse",
                            help="Monto realmente gastado hasta hoy"
                        )
                    with col3:
                        st.metric(
                            "📊 Variación",
                            f"S/. {variacion:,.2f}",
                            delta="Adelanto" if variacion < 0 else "Atraso",
                            delta_color="normal" if variacion < 0 else "inverse",
                            help="Diferencia entre real y plan (negativo = adelanto)"
                        )
                    with col4:
                        if spi > 0:
                            estado_spi = "🟢 Óptimo" if spi <= 0.95 else ("🟡 Aceptable" if spi <= 1.05 else "🔴 Crítico")
                        else:
                            estado_spi = "⚪ Sin datos"
                        st.metric(
                            "⚡ Índice SPI",
                            f"{spi:.2f}",
                            delta=estado_spi,
                            help="Índice de desempeño. <1: adelanto | =1: en línea | >1: atraso"
                        )

                    # Gráfico
                    st.markdown("#### 📈 Gráfico de Curva S")
                    st.line_chart(
                        df_plot[["plan_acum", "real_acum"]].rename(columns={
                            "plan_acum": "Plan Acumulado",
                            "real_acum": "Real Acumulado"
                        }),
                        use_container_width=True,
                        height=400
                    )
                    
                    # Tabla detallada
                    with st.expander("📋 Ver Tabla Detallada de Datos"):
                        df_tabla = df_plot.reset_index()
                        df_tabla["variacion"] = df_tabla["real_acum"] - df_tabla["plan_acum"]
                        
                        st.dataframe(
                            df_tabla.rename(columns={
                                "fecha": "Fecha",
                                "plan_dia": "Plan Día",
                                "real_dia": "Real Día",
                                "plan_acum": "Plan Acum.",
                                "real_acum": "Real Acum.",
                                "variacion": "Variación"
                            }),
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Plan Día": st.column_config.NumberColumn(format="S/. %.2f"),
                                "Real Día": st.column_config.NumberColumn(format="S/. %.2f"),
                                "Plan Acum.": st.column_config.NumberColumn(format="S/. %.2f"),
                                "Real Acum.": st.column_config.NumberColumn(format="S/. %.2f"),
                                "Variación": st.column_config.NumberColumn(format="S/. %.2f"),
                            }
                        )

            # ===== TAB: HITOS DE PAGO =====
            with tab_hitos:
                st.markdown("### 💰 Gestión de Hitos de Pago")
                st.caption("Registra y controla los hitos de pago del proyecto (valorizaciones, adelantos, liquidaciones)")

                if "form_hito_counter" not in st.session_state:
                    st.session_state.form_hito_counter = 0

                # Formulario para agregar hito
                st.markdown("#### ➕ Registrar Nuevo Hito de Pago")
                
                with st.form(key=f"form_add_hito_{st.session_state.form_hito_counter}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        h_desc = st.text_input(
                            "📋 Descripción del Hito*",
                            placeholder="Ej: Valorización N°01, Adelanto Materiales, Liquidación Final"
                        )
                    with c2:
                        h_monto = st.number_input(
                            "💵 Monto (S/.)*",
                            min_value=0.0,
                            step=100.0,
                            format="%.2f"
                        )
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        h_fecha = st.date_input("📅 Fecha Estimada*", value=date.today())
                    with c2:
                        h_estado = st.selectbox(
                            "Estado",
                            ["Pendiente", "Pagado"],
                            help="Marca como Pagado si ya se procesó el pago"
                        )

                    h_obs = st.text_area(
                        "📝 Observación (opcional)",
                        placeholder="Sustento enviado, OC aprobada, documento por adjuntar, etc.",
                        height=80
                    )

                    if st.form_submit_button(f"✅ Agregar Hito ({h_estado})", use_container_width=True, type="primary"):
                        ok, msg = validar_hito_pago(h_desc, h_fecha, h_monto)
                        if not ok:
                            st.error(f"❌ {msg}")
                        else:
                            hito = {
                                "descripcion": h_desc.strip(),
                                "fecha": str(h_fecha),
                                "monto": float(h_monto),
                                "estado": h_estado,
                                "observacion": h_obs.strip(),
                                "creado_por": st.session_state.get("usuario_logueado", "jefe"),
                            }
                            ok2, msg2 = agregar_hito_pago(obra_codigo, hito)
                            if ok2:
                                st.success("✅ Hito de pago registrado correctamente")
                                st.session_state.form_hito_counter += 1
                                st.rerun()
                            else:
                                st.error(f"❌ {msg2}")

                st.divider()

                # Lista de hitos
                hitos = obtener_hitos_pago_obra(obra_codigo) or []
                for h in hitos:
                    h.setdefault("estado", "Pendiente")
                    h.setdefault("creado_por", "jefe")

                if not hitos:
                    st.info("📭 No hay hitos de pago registrados. Agrega el primer hito para comenzar el seguimiento.")
                else:
                    # Resumen de hitos
                    resumen_h = calcular_resumen_hitos(hitos)
                    
                    st.markdown("#### 💼 Resumen Financiero de Hitos")
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric(
                            "💰 Total Hitos",
                            f"S/. {resumen_h['total_hitos']:,.2f}",
                            help="Suma total de todos los hitos de pago"
                        )
                    with col2:
                        st.metric(
                            "✅ Pagado",
                            f"S/. {resumen_h['pagado']:,.2f}",
                            help="Monto de hitos ya pagados"
                        )
                    with col3:
                        st.metric(
                            "⏳ Pendiente",
                            f"S/. {resumen_h['pendiente']:,.2f}",
                            help="Monto de hitos pendientes por pagar"
                        )
                    with col4:
                        porc_pagado = (resumen_h['pagado'] / resumen_h['total_hitos'] * 100) if resumen_h['total_hitos'] > 0 else 0
                        st.metric(
                            "📊 % Pagado",
                            f"{porc_pagado:.1f}%",
                            help="Porcentaje de hitos pagados"
                        )
                    
                    # Barra de progreso
                    if resumen_h['total_hitos'] > 0:
                        progress_hitos = resumen_h['pagado'] / resumen_h['total_hitos']
                        st.progress(progress_hitos)
                        st.caption(f"S/. {resumen_h['pagado']:,.2f} de S/. {resumen_h['total_hitos']:,.2f} pagados")

                    st.markdown(f"#### 📋 Listado de Hitos ({len(hitos)} total)")
                    
                    # Filtro
                    col1, col2 = st.columns([1, 3])
                    with col1:
                        filtro_hitos = st.selectbox(
                            "Filtrar por estado:",
                            ["Todos", "Pendiente", "Pagado"],
                            key="filtro_hitos"
                        )
                    
                    hitos_filtrados = hitos if filtro_hitos == "Todos" else [h for h in hitos if h.get("estado") == filtro_hitos]
                    
                    # Tabla de hitos
                    if hitos_filtrados:
                        dfh = pd.DataFrame(hitos_filtrados)
                        cols_mostrar = ["descripcion", "fecha", "monto", "estado", "observacion"]
                        cols_existentes = [c for c in cols_mostrar if c in dfh.columns]
                        
                        st.dataframe(
                            dfh[cols_existentes].rename(columns={
                                "descripcion": "Descripción",
                                "fecha": "Fecha",
                                "monto": "Monto (S/.)",
                                "estado": "Estado",
                                "observacion": "Observación",
                            }),
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                "Monto (S/.)": st.column_config.NumberColumn(format="S/. %.2f"),
                                "Estado": st.column_config.TextColumn(
                                    width="small",
                                )
                            }
                        )
                    else:
                        st.info(f"No hay hitos con estado '{filtro_hitos}'")

                    st.markdown("#### ✏️ Editar / Cambiar Estado / Eliminar Hito")
                    
                    if "idx_hito_sel" not in st.session_state:
                        st.session_state.idx_hito_sel = 0

                    opciones_h = [
                        f"{h.get('descripcion', '(sin descripción)')} - S/. {float(h.get('monto', 0) or 0):,.2f} [{h.get('estado', 'Pendiente')}]"
                        for h in hitos
                    ]
                    
                    sel_h = st.selectbox(
                        "Selecciona un hito para editar:",
                        opciones_h,
                        index=min(st.session_state.idx_hito_sel, len(opciones_h) - 1),
                        key="sel_hito"
                    )
                    st.session_state.idx_hito_sel = opciones_h.index(sel_h)

                    h_sel = hitos[st.session_state.idx_hito_sel]
                    h_id = h_sel.get("id")

                    # Botón rápido para marcar como pagado
                    if h_sel.get("estado") == "Pendiente":
                        if st.button("✅ Marcar como PAGADO", use_container_width=True, type="primary"):
                            payload = dict(h_sel)
                            payload["estado"] = "Pagado"
                            ok, msg = actualizar_hito_pago(obra_codigo, h_id, payload)
                            if ok:
                                st.success("✅ Hito marcado como Pagado")
                                st.rerun()
                            else:
                                st.error(f"❌ {msg}")

                    with st.form("form_edit_hito"):
                        c1, c2 = st.columns(2)
                        with c1:
                            e_desc = st.text_input("Descripción", value=h_sel.get("descripcion", ""))
                        with c2:
                            e_monto = st.number_input(
                                "Monto (S/.)",
                                min_value=0.0,
                                step=100.0,
                                format="%.2f",
                                value=float(h_sel.get("monto", 0.0) or 0.0)
                            )
                        
                        c1, c2 = st.columns(2)
                        with c1:
                            e_fecha = st.date_input("Fecha", value=pd.to_datetime(h_sel.get("fecha", date.today())).date())
                        with c2:
                            e_estado = st.selectbox(
                                "Estado",
                                ["Pendiente", "Pagado"],
                                index=0 if h_sel.get("estado") == "Pendiente" else 1
                            )

                        e_obs = st.text_area("Observación", value=h_sel.get("observacion", ""), height=80)

                        colx, coly = st.columns(2)
                        with colx:
                            guardar_h = st.form_submit_button("💾 Guardar Cambios", use_container_width=True, type="primary")
                        with coly:
                            eliminar_h = st.form_submit_button("🗑️ Eliminar Hito", use_container_width=True)

                    if guardar_h:
                        ok, msg = validar_hito_pago(e_desc, e_fecha, e_monto)
                        if not ok:
                            st.error(f"❌ {msg}")
                        else:
                            payload = {
                                "descripcion": e_desc.strip(),
                                "fecha": str(e_fecha),
                                "monto": float(e_monto),
                                "estado": e_estado,
                                "observacion": e_obs.strip(),
                                "creado_por": h_sel.get("creado_por", "jefe"),
                            }
                            ok2, msg2 = actualizar_hito_pago(obra_codigo, h_id, payload)
                            if ok2:
                                st.success("✅ Hito actualizado correctamente")
                                st.rerun()
                            else:
                                st.error(f"❌ {msg2}")

                    if eliminar_h:
                        ok3, msg3 = eliminar_hito_pago(obra_codigo, h_id)
                        if ok3:
                            st.success("✅ Hito eliminado correctamente")
                            st.session_state.idx_hito_sel = 0
                            st.rerun()
                        else:
                            st.error(f"❌ {msg3}")

            # ===== TAB: PRÓXIMAMENTE =====
            with tab4:
                st.markdown("### 🚧 Próximamente")
                st.info("⚙️ Esta sección está en desarrollo. Próximas funcionalidades incluirán:")
                st.markdown("""
                - 📊 **Dashboard analítico** con gráficos interactivos
                - 📝 **Reportes PDF** descargables
                - 📧 **Notificaciones automáticas** por correo
                - 📱 **Exportación de datos** a Excel
                - 🔔 **Alertas personalizadas** de presupuesto y plazos
                """)

    # ==================== PANTALLA DE BIENVENIDA ====================
    else:
        st.markdown("""
        ## Bienvenido al Sistema de Control de Obras

        ### Selecciona una obra del panel lateral para comenzar

        ---

        ### Resumen General
        """)

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total de Obras", len(obras))
        with col2:
            st.metric("Última Actualización", date.today().strftime("%d/%m/%Y"))

# ==================== MODO PASANTE ====================
else:
    with st.sidebar:
        st.image("img/logo.png", use_container_width=True)
        st.divider()

        obras = cargar_obras()
        usuario_pasante = st.session_state.get("auth", "")

        obra_cod, obra_nom = obtener_obra_asignada_pasante(obras, usuario_pasante)
        if not obra_cod:
            st.error(
                "Este pasante no tiene una obra asignada (no se encontró coincidencia). "
                "Verifica que el código o nombre de la obra contenga: "
                "'ventanilla/pachacutec' o 'molina/rinconada'."
            )
            st.stop()

        if "obra_seleccionada" not in st.session_state or st.session_state.obra_seleccionada != obra_cod:
            st.session_state.obra_seleccionada = obra_cod
            st.session_state.mostrar_form_obra = False
            st.session_state.mostrar_insumos = False
            st.rerun()

        st.subheader("Obra asignada")
        st.info(f"{obra_nom}")
        st.caption("Rol: PASANTE (solo puede ver su obra asignada)")

    st.title("Modo Pasante")

    if st.session_state.obra_seleccionada:
        obra_codigo = st.session_state.obra_seleccionada
        obra_nombre = obras.get(obra_codigo, "Obra no encontrada")
        st.header(f"{obra_nombre}")

        presupuesto = obtener_presupuesto_obra(obra_codigo)
        avances = obtener_avances_obra(obra_codigo)
        resumen = calcular_resumen_presupuesto(presupuesto, avances)

        st.markdown("### 💰 Resumen de Presupuesto (lectura)")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Presupuestado", f"S/. {resumen['presupuestado']:,.2f}")
        with col2:
            st.metric("Gastado", f"S/. {resumen['gastado']:,.2f}", delta=f"{resumen['porcentaje_gastado']:.1f}%")
        with col3:
            st.metric("Disponible", f"S/. {resumen['disponible']:,.2f}")
        with col4:
            porcentaje = resumen['porcentaje_gastado']
            if porcentaje < 50:
                estado = "🟢 Saludable"
            elif porcentaje < 80:
                estado = "🟡 Moderado"
            elif porcentaje < 100:
                estado = "🟠 Crítico"
            else:
                estado = "🔴 Excedido"
            st.metric("Estado", estado)

        st.progress(min(resumen['porcentaje_gastado'] / 100, 1.0))
        st.divider()

        eficiencia_promedio = calcular_eficiencia_promedio_obra(avances)
        emoji_rendimiento, texto_rendimiento, _ = obtener_estado_rendimiento(eficiencia_promedio)

        st.markdown("### 📊 Rendimiento de Mano de Obra (lectura)")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Eficiencia Promedio", f"{eficiencia_promedio:.1f}%")
        with c2:
            st.metric("Estado", f"{emoji_rendimiento} {texto_rendimiento}")
        with c3:
            if eficiencia_promedio >= 100:
                st.metric("Superávit", f"+{eficiencia_promedio - 100:.1f}%")
            else:
                st.metric("Déficit", f"-{100 - eficiencia_promedio:.1f}%")

        if eficiencia_promedio > 0:
            st.progress(min(eficiencia_promedio / 100, 1.0))
        st.divider()

        tab1, tab2, tab3 = st.tabs(["Parte Diario", "Historial de Avances", "Cronograma Valorizado"])

        # ==================== TAB 1: PARTE DIARIO (PASANTE) ====================
        with tab1:
            st.subheader("Parte Diario del Día")
            hoy = date.today()

            if "form_parte_diario_counter" not in st.session_state:
                st.session_state.form_parte_diario_counter = 0

            if "insumos_mo_confirmados" not in st.session_state:
                st.session_state.insumos_mo_confirmados = []
            if "insumos_mat_confirmados" not in st.session_state:
                st.session_state.insumos_mat_confirmados = []
            if "insumos_eq_confirmados" not in st.session_state:
                st.session_state.insumos_eq_confirmados = []
            if "insumos_otros_confirmados" not in st.session_state:
                st.session_state.insumos_otros_confirmados = []

            counter = st.session_state.form_parte_diario_counter

            st.markdown("### Información General")
            col1, col2 = st.columns(2)

            with col1:
                nombre_default = st.session_state.get("usuario_logueado", "Usuario")
                responsable = st.text_input("Tu nombre", value=nombre_default, key=f"responsable_input_pas_{counter}")

            with col2:
                avance = st.slider("Avance logrado hoy (%)", 0, 30, 5, key=f"avance_input_pas_{counter}")

            col1, col2 = st.columns(2)
            with col1:
                name_partida = st.text_input(
                    "Nombre de la partida o actividad realizada hoy",
                    placeholder="ej: Cimentación, Estructura, Albañilería, etc.",
                    key=f"name_partida_input_pas_{counter}"
                )
            with col2:
                c21, c22 = st.columns(2)
                with c21:
                    cantidad_ejecutada = st.number_input(
                        "Metrado Ejecutado",
                        min_value=0.0,
                        step=0.1,
                        placeholder="Ingresa la cantidad realizada",
                        key=f"cantidad_ejecutada_pas_{counter}"
                    )
                with c22:
                    unidad_medida = st.text_input(
                        "Unidad",
                        placeholder="ej: M3, KG, UND, HH",
                        key=f"unidad_input_pas_{counter}"
                    )

            col1, col2 = st.columns(2)
            with col1:
                horas_mano_obra = st.number_input("Jornada Laboral (h)", min_value=0, step=1, value=8, key=f"horas_input_pas_{counter}")
            with col2:
                rendimiento_partida = st.number_input(
                    "Rendimiento Esperado de la Partida (por día)",
                    min_value=0.0,
                    step=0.1,
                    value=6.0,
                    help="Rendimiento en unidad/día. Se ajusta proporcionalmente si la jornada no es de 8 horas.",
                    key=f"rendimiento_input_pas_{counter}"
                )

            st.markdown("### Costos")
            insumos_lista = cargar_insumos()

            tab_mo, tab_mat, tab_eq, tab_otros = st.tabs(["Mano de Obra", "Materiales", "Equipos", "Otros"])

            with tab_mo:
                insumos_mo = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "mano de obra"]
                if not insumos_mo:
                    st.info(_categoria_vacia("Mano de Obra"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns([1.5, 0.5, 0.5])
                    with col1:
                        desc_mano_obra = st.selectbox("Descripción", options=insumos_mo, key=f"desc_mo_pas_{counter}")
                    with col2:
                        cant_mano_obra = st.number_input(
                            "Cuadrilla",
                            min_value=0.0,
                            step=0.1,
                            value=1.0,
                            format="%.2f",
                            key=f"cant_mo_pas_{counter}"
                        )
                    with col3:
                        precio_mano_obra = obtener_precio_insumo(insumos_lista, desc_mano_obra)
                        st.metric("Precio Unitario", f"S/. {precio_mano_obra:.2f}")

                    if st.button("Confirmar Mano de Obra", use_container_width=True, type="primary", key=f"btn_confirmar_mo_pas_{counter}"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_mano_obra <= 0:
                            st.error("❌ La cuadrilla debe ser mayor a 0")
                        else:
                            cantidad_hh = calcular_cantidad_hh(cant_mano_obra, horas_mano_obra, rendimiento_partida)
                            parcial_mo = calcular_parcial(cantidad_hh, precio_mano_obra)
                            st.session_state.insumos_mo_confirmados.append({
                                "Descripción": desc_mano_obra,
                                "Cuadrilla": cant_mano_obra,
                                "Precio Unit.": precio_mano_obra,
                                "Cantidad (HH)": cantidad_hh,
                                "Parcial (S/)": parcial_mo
                            })
                            st.success(f"✓ {desc_mano_obra} agregado")
                            st.rerun()

            with tab_mat:
                insumos_mat = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "materiales"]
                if not insumos_mat:
                    st.info(_categoria_vacia("Materiales"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_materiales = st.selectbox("Descripción", options=insumos_mat, key=f"desc_mat_pas_{counter}")
                    with col2:
                        cant_materiales = st.number_input("Cantidad", min_value=0.0000, step=0.0010, format="%.4f", key=f"cant_mat_pas_{counter}")
                    with col3:
                        precio_materiales = obtener_precio_insumo(insumos_lista, desc_materiales)
                        st.metric("Precio Unitario", f"S/. {precio_materiales:.2f}")

                    if st.button("Confirmar Material", use_container_width=True, type="primary", key=f"btn_confirmar_mat_pas_{counter}"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_materiales <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_mat = calcular_parcial(cant_materiales, precio_materiales)
                            st.session_state.insumos_mat_confirmados.append({
                                "Descripción": desc_materiales,
                                "Cantidad": cant_materiales,
                                "Precio Unit.": precio_materiales,
                                "Parcial (S/)": parcial_mat
                            })
                            st.success(f"✓ {desc_materiales} agregado")
                            st.rerun()

            with tab_eq:
                insumos_eq = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "equipos"]
                if not insumos_eq:
                    st.info(_categoria_vacia("Equipos"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_equipos = st.selectbox("Descripción", options=insumos_eq, key=f"desc_eq_pas_{counter}")
                    with col2:
                        cant_equipos = st.number_input("Cantidad", min_value=0.0, step=1.0, key=f"cant_eq_pas_{counter}")
                    with col3:
                        precio_equipos = obtener_precio_insumo(insumos_lista, desc_equipos)
                        st.metric("Precio Unitario", f"S/. {precio_equipos:.2f}")

                    if st.button("Confirmar Equipo", use_container_width=True, type="primary", key=f"btn_confirmar_eq_pas_{counter}"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_equipos <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_eq = calcular_parcial(cant_equipos, precio_equipos)
                            st.session_state.insumos_eq_confirmados.append({
                                "Descripción": desc_equipos,
                                "Cantidad": cant_equipos,
                                "Precio Unit.": precio_equipos,
                                "Parcial (S/)": parcial_eq
                            })
                            st.success(f"✓ {desc_equipos} agregado")
                            st.rerun()

            with tab_otros:
                insumos_otros = [i["Insumo"] for i in insumos_lista if i.get("Tipo") == "otros"]
                if not insumos_otros:
                    st.info(_categoria_vacia("Otros"))
                else:
                    st.markdown("#### Ingresar Datos")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        desc_otros = st.selectbox("Descripción", options=insumos_otros, key=f"desc_otros_pas_{counter}")
                    with col2:
                        cant_otros = st.number_input("Cantidad", min_value=0.0, step=0.01, key=f"cant_otros_pas_{counter}")
                    with col3:
                        precio_otros = obtener_precio_insumo(insumos_lista, desc_otros)
                        st.metric("Precio Unitario", f"S/. {precio_otros:.2f}")

                    if st.button("Confirmar Otro", use_container_width=True, type="primary", key=f"btn_confirmar_otros_pas_{counter}"):
                        if rendimiento_partida <= 0:
                            st.error("❌ El rendimiento de la partida debe ser mayor a 0")
                        elif not unidad_medida.strip():
                            st.error("❌ Debes especificar la unidad de medida")
                        elif cant_otros <= 0:
                            st.error("❌ La cantidad debe ser mayor a 0")
                        else:
                            parcial_otros = calcular_parcial(cant_otros, precio_otros)
                            st.session_state.insumos_otros_confirmados.append({
                                "Descripción": desc_otros,
                                "Cantidad": cant_otros,
                                "Precio Unit.": precio_otros,
                                "Parcial (S/)": parcial_otros
                            })
                            st.success(f"✓ {desc_otros} agregado")
                            st.rerun()

            st.markdown("### 📊 Resumen de Costos Consolidado")
            total_mo = sum([item["Parcial (S/)"] for item in st.session_state.insumos_mo_confirmados])
            total_mat = sum([item["Parcial (S/)"] for item in st.session_state.insumos_mat_confirmados])
            total_eq = sum([item["Parcial (S/)"] for item in st.session_state.insumos_eq_confirmados])
            total_otros = sum([item["Parcial (S/)"] for item in st.session_state.insumos_otros_confirmados])
            total_general = total_mo + total_mat + total_eq + total_otros
            total_general_ejecutado = total_general * cantidad_ejecutada if cantidad_ejecutada > 0 else 0

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Mano de Obra", f"S/. {total_mo:.2f}")
            with col2:
                st.metric("Materiales", f"S/. {total_mat:.2f}")
            with col3:
                st.metric("Equipos", f"S/. {total_eq:.2f}")
            with col4:
                st.metric("Otros", f"S/. {total_otros:.2f}")

            col1, col2 = st.columns(2)
            with col1:
                unidad_label = unidad_medida if unidad_medida.strip() else "unidad"
                st.metric(f"TOTAL/{unidad_label}", f"S/. {total_general:.2f}")
            with col2:
                st.metric("TOTAL EJECUTADO", f"S/. {total_general_ejecutado:.2f}", delta_color="off")

            st.markdown("### Finalizar Parte Diario")
            obs = st.text_area("Observaciones", key=f"obs_final_pas_{counter}")
            fotos = st.file_uploader("Fotos del avance", accept_multiple_files=True, type=["jpg", "png", "jpeg"], key=f"fotos_final_pas_{counter}")

            st.session_state["cantidad_ejecutada_cache"] = cantidad_ejecutada
            st.session_state["unidad_medida_cache"] = unidad_medida

            if 0 < len(fotos) < 3:
                st.warning("⚠️ Debes subir mínimo 3 fotos")

            @st.dialog("Confirmar Envío de Parte Diario")
            def confirmar_envio_modal_pasante():
                st.warning("⚠️ ¿Estás seguro de enviar el parte diario?")
                st.write("Esta acción guardará el registro y limpiará todos los campos.")

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ SÍ, ENVIAR", use_container_width=True, type="primary", key=f"si_enviar_pas_{counter}"):
                        cantidad_ejecutada_cache = st.session_state.get("cantidad_ejecutada_cache", 0)

                        totales = calcular_totales_costos(
                            st.session_state.insumos_mo_confirmados,
                            st.session_state.insumos_mat_confirmados,
                            st.session_state.insumos_eq_confirmados,
                            st.session_state.insumos_otros_confirmados,
                            cantidad_ejecutada=cantidad_ejecutada_cache if cantidad_ejecutada_cache > 0 else 1
                        )

                        rutas_fotos = guardar_fotos_avance(obra_codigo, fotos, hoy)

                        nuevo_avance = crear_avance_dict(
                            fecha=hoy,
                            responsable=responsable,
                            avance_pct=avance,
                            observaciones=obs,
                            rutas_fotos=rutas_fotos,
                            nombre_partida=name_partida,
                            rendimiento_partida=rendimiento_partida,
                            unidad_medida=st.session_state.get("unidad_medida_cache", unidad_medida),
                            horas_mano_obra=horas_mano_obra,
                            cantidad_ejecutada=cantidad_ejecutada_cache,
                            insumos_mo=st.session_state.insumos_mo_confirmados,
                            insumos_mat=st.session_state.insumos_mat_confirmados,
                            insumos_eq=st.session_state.insumos_eq_confirmados,
                            insumos_otros=st.session_state.insumos_otros_confirmados,
                            totales=totales
                        )

                        exito, mensaje_db = agregar_avance(obra_codigo, nuevo_avance)
                        if not exito:
                            st.error(f"❌ Error al guardar: {mensaje_db}")
                            return

                        st.session_state.insumos_mo_confirmados = []
                        st.session_state.insumos_mat_confirmados = []
                        st.session_state.insumos_eq_confirmados = []
                        st.session_state.insumos_otros_confirmados = []

                        st.session_state.form_parte_diario_counter += 1
                        st.success("✅ ¡Parte diario enviado correctamente!")
                        st.balloons()
                        st.rerun()

                with c2:
                    if st.button("❌ CANCELAR", use_container_width=True, type="secondary", key=f"cancelar_pas_{counter}"):
                        st.rerun()

            if st.button("📤 ENVIAR PARTE DIARIO", use_container_width=True, type="primary", key=f"enviar_final_pas_{counter}"):
                es_valido, errores = validar_parte_diario_completo(responsable, avance, rendimiento_partida, unidad_medida, fotos)
                costos_validos, mensaje_costos = validar_costos_parte_diario(
                    st.session_state.insumos_mo_confirmados,
                    st.session_state.insumos_mat_confirmados,
                    st.session_state.insumos_eq_confirmados,
                    st.session_state.insumos_otros_confirmados
                )

                if not es_valido:
                    for error in errores:
                        st.error(f"❌ {error}")
                elif not costos_validos:
                    st.error(f"❌ {mensaje_costos}")
                else:
                    confirmar_envio_modal_pasante()

        # ==================== TAB 2: HISTORIAL (PASANTE) ====================
        with tab2:
            st.subheader("Historial de Avances")
            historial = preparar_historial_avances(obra_codigo)

            if historial:
                for item in historial:
                    with st.expander(f"📅 {item['fecha_fmt']} - {item['responsable']} ({item['avance_pct']}%)"):
                        st.write("**Responsable:**", item["responsable"])
                        st.write("**Avance del día:**", f"{item['avance_pct']}%")
                        if item.get("obs"):
                            st.markdown("### 📝 Observaciones")
                            st.write(item["obs"])
                        if item.get("fotos"):
                            st.markdown("### 📷 Fotos del avance")
                            cols = st.columns(min(len(item["fotos"]), 3))
                            for i, foto_path in enumerate(item["fotos"]):
                                target = cols[i % 3]
                                if foto_path and os.path.exists(foto_path):
                                    target.image(foto_path, caption=os.path.basename(foto_path))
                                else:
                                    target.warning(f"No se encontró la imagen: {os.path.basename(foto_path) if foto_path else 'Archivo no especificado'}")
            else:
                st.info("No hay partes diarios registrados para esta obra aún.")

        # ==================== TAB 3: CRONOGRAMA (PASANTE) ====================
        with tab3:
            st.subheader("Cronograma Valorizado (Pasante)")
            st.caption("Puedes registrar Partidas e Hitos como PENDIENTE. El JEFE los aprueba/edita. Puedes eliminar lo que tú mismo registraste si te equivocas (recomendado: solo si sigue Pendiente).")

            usuario_actual = st.session_state.get("usuario_logueado") or st.session_state.get("auth") or "pasante"

            cronograma_all = obtener_cronograma_obra(obra_codigo) or []
            hitos_all = obtener_hitos_pago_obra(obra_codigo) or []

            for it in cronograma_all:
                it.setdefault("estado", "Aprobado")
                it.setdefault("creado_por", "jefe")
            for h in hitos_all:
                h.setdefault("estado", "Pendiente")
                h.setdefault("creado_por", "jefe")

            st.markdown("### 1) Partidas del Cronograma (Solicitudes)")
            if "form_crono_counter_pas" not in st.session_state:
                st.session_state.form_crono_counter_pas = 0

            with st.form(key=f"form_add_crono_pas_{st.session_state.form_crono_counter_pas}"):
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                with c1:
                    crono_nombre = st.text_input("Partida", placeholder="Ej: Cimentación")
                with c2:
                    crono_inicio = st.date_input("Inicio", value=date.today())
                with c3:
                    crono_fin = st.date_input("Fin", value=date.today())
                with c4:
                    crono_monto = st.number_input("Monto (S/.)", min_value=0.0, step=0.01, format="%.2f")
                crono_desc = st.text_input("Descripción (opcional)", placeholder="Ej: concreto f'c 210")

                if st.form_submit_button("Enviar Solicitud (Pendiente)", use_container_width=True, type="primary"):
                    ok, msg = validar_partida_cronograma(crono_nombre, crono_inicio, crono_fin, crono_monto)
                    if not ok:
                        st.error(f"❌ {msg}")
                    else:
                        partida = {
                            "nombre": crono_nombre.strip(),
                            "fecha_inicio": str(crono_inicio),
                            "fecha_fin": str(crono_fin),
                            "monto_planificado": float(crono_monto),
                            "descripcion": crono_desc.strip(),
                            "estado": "Pendiente",
                            "creado_por": usuario_actual,
                        }
                        ok2, msg2 = agregar_partida_cronograma(obra_codigo, partida)
                        if ok2:
                            st.success("✅ Solicitud enviada (Pendiente).")
                            st.session_state.form_crono_counter_pas += 1
                            st.rerun()
                        else:
                            st.error(f"❌ {msg2}")

            cronograma_all = obtener_cronograma_obra(obra_codigo) or []
            for it in cronograma_all:
                it.setdefault("estado", "Aprobado")
                it.setdefault("creado_por", "jefe")

            if cronograma_all:
                dfc = pd.DataFrame(cronograma_all)
                cols_pref = ["estado", "creado_por", "nombre", "fecha_inicio", "fecha_fin", "monto_planificado", "descripcion"]
                cols = [c for c in cols_pref if c in dfc.columns] + [c for c in dfc.columns if c not in cols_pref]
                st.dataframe(
                    dfc[cols].rename(columns={
                        "estado": "Estado",
                        "creado_por": "Creado por",
                        "nombre": "Partida",
                        "fecha_inicio": "Inicio",
                        "fecha_fin": "Fin",
                        "monto_planificado": "Monto Planificado (S/.)",
                        "descripcion": "Descripción",
                    }),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("Aún no hay partidas registradas en la obra.")

            propias_pendientes = [it for it in cronograma_all if it.get("creado_por") == usuario_actual and it.get("estado") == "Pendiente"]
            if propias_pendientes:
                st.markdown("#### Eliminar Partida (solo tus solicitudes Pendiente)")
                opciones_del = [
                    f"{i+1}. {it.get('nombre','(sin nombre)')} | {it.get('fecha_inicio','')} → {it.get('fecha_fin','')} | S/. {float(it.get('monto_planificado',0) or 0):.2f}"
                    for i, it in enumerate(propias_pendientes)
                ]
                if "idx_del_crono_pas" not in st.session_state:
                    st.session_state.idx_del_crono_pas = 0

                sel_del = st.selectbox(
                    "Selecciona tu partida Pendiente",
                    opciones_del,
                    index=min(st.session_state.idx_del_crono_pas, len(opciones_del)-1),
                    key="sel_del_crono_pas"
                )
                st.session_state.idx_del_crono_pas = opciones_del.index(sel_del)
                item_del = propias_pendientes[st.session_state.idx_del_crono_pas]
                pid = item_del.get("id")

                if st.button("🗑️ Eliminar Partida Seleccionada", use_container_width=True, type="secondary", key="btn_del_crono_pas"):
                    if not pid:
                        st.error("❌ No se encontró ID para eliminar esta partida.")
                    else:
                        okd, msgd = eliminar_partida_cronograma(obra_codigo, pid)
                        if okd:
                            st.success("✅ Partida eliminada.")
                            st.session_state.idx_del_crono_pas = 0
                            st.rerun()
                        else:
                            st.error(f"❌ {msgd}")

            st.divider()

            st.markdown("### 2) Curva S (Plan vs Real)")
            st.caption("El Plan usa SOLO partidas Aprobadas. Tus Pendientes no entran en la curva hasta que el JEFE apruebe.")
            freq = st.selectbox("Agrupar por", ["Semanal", "Mensual", "Diario"], index=0, key="freq_curva_s_pas")

            cronograma_aprob = [it for it in cronograma_all if it.get("estado") == "Aprobado"]
            plan_df = construir_curva_s_planificada(cronograma_aprob, freq=freq)
            real_df = construir_curva_s_real(avances, freq=freq)

            if plan_df.empty and real_df.empty:
                st.info("No hay datos suficientes para graficar. Registra cronograma y/o partes diarios.")
            else:
                df_plot = pd.merge(plan_df, real_df, on="fecha", how="outer").fillna(0).sort_values("fecha")
                df_plot = df_plot.set_index("fecha")

                if "plan_acum" not in df_plot.columns:
                    df_plot["plan_acum"] = df_plot.get("plan_dia", 0).cumsum()
                if "real_acum" not in df_plot.columns:
                    df_plot["real_acum"] = df_plot.get("real_dia", 0).cumsum()

                st.line_chart(df_plot[["plan_acum", "real_acum"]], use_container_width=True)
                st.dataframe(df_plot.reset_index(), use_container_width=True, hide_index=True)

            st.divider()

            st.markdown("### 3) Hitos de Pago (Solicitudes)")
            st.caption("Se guardan como Pendiente. Puedes eliminar solo los que tú creaste y estén Pendiente.")

            if "form_hito_counter_pas" not in st.session_state:
                st.session_state.form_hito_counter_pas = 0

            with st.form(key=f"form_add_hito_pas_{st.session_state.form_hito_counter_pas}"):
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    h_desc = st.text_input("Descripción", placeholder="Ej: Valorización N°01")
                with c2:
                    h_fecha = st.date_input("Fecha", value=date.today())
                with c3:
                    h_monto = st.number_input("Monto (S/.)", min_value=0.0, step=0.01, format="%.2f")

                h_obs = st.text_input("Observación (opcional)", placeholder="Ej: Sustento enviado / OC pendiente")

                if st.form_submit_button("Agregar Hito (Pendiente)", use_container_width=True, type="primary"):
                    ok, msg = validar_hito_pago(h_desc, h_fecha, h_monto)
                    if not ok:
                        st.error(f"❌ {msg}")
                    else:
                        hito = {
                            "descripcion": h_desc.strip(),
                            "fecha": str(h_fecha),
                            "monto": float(h_monto),
                            "estado": "Pendiente",
                            "observacion": h_obs.strip(),
                            "creado_por": usuario_actual,
                        }
                        ok2, msg2 = agregar_hito_pago(obra_codigo, hito)
                        if ok2:
                            st.success("✅ Hito registrado (Pendiente).")
                            st.session_state.form_hito_counter_pas += 1
                            st.rerun()
                        else:
                            st.error(f"❌ {msg2}")

            hitos_all = obtener_hitos_pago_obra(obra_codigo) or []
            for h in hitos_all:
                h.setdefault("estado", "Pendiente")
                h.setdefault("creado_por", "jefe")

            if hitos_all:
                dfh = pd.DataFrame(hitos_all)
                cols_pref = ["descripcion", "fecha", "monto", "estado", "observacion", "creado_por"]
                cols = [c for c in cols_pref if c in dfh.columns] + [c for c in dfh.columns if c not in cols_pref]
                st.dataframe(
                    dfh[cols].rename(columns={
                        "descripcion": "Hito",
                        "fecha": "Fecha",
                        "monto": "Monto (S/.)",
                        "estado": "Estado",
                        "observacion": "Observación",
                        "creado_por": "Creado por",
                    }),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("Aún no hay hitos registrados en la obra.")

            propias_h_pend = [h for h in hitos_all if h.get("creado_por") == usuario_actual and h.get("estado") == "Pendiente"]
            if propias_h_pend:
                st.markdown("#### Eliminar Hito (solo tus Pendientes)")
                opciones_hdel = [
                    f"{i+1}. {it.get('descripcion','(sin descripción)')} | {it.get('fecha','')} | S/. {float(it.get('monto',0) or 0):.2f}"
                    for i, it in enumerate(propias_h_pend)
                ]
                if "idx_del_hito_pas" not in st.session_state:
                    st.session_state.idx_del_hito_pas = 0

                sel_hdel = st.selectbox(
                    "Selecciona tu hito Pendiente",
                    opciones_hdel,
                    index=min(st.session_state.idx_del_hito_pas, len(opciones_hdel)-1),
                    key="sel_del_hito_pas"
                )
                st.session_state.idx_del_hito_pas = opciones_hdel.index(sel_hdel)
                h_del = propias_h_pend[st.session_state.idx_del_hito_pas]
                hid = h_del.get("id")

                if st.button("🗑️ Eliminar Hito Seleccionado", use_container_width=True, type="secondary", key="btn_del_hito_pas"):
                    if not hid:
                        st.error("❌ No se encontró ID para eliminar este hito.")
                    else:
                        okd, msgd = eliminar_hito_pago(obra_codigo, hid)
                        if okd:
                            st.success("✅ Hito eliminado.")
                            st.session_state.idx_del_hito_pas = 0
                            st.rerun()
                        else:
                            st.error(f"❌ {msgd}")

    else:
        st.markdown("## Bienvenido (Modo Pasante)\nSelecciona una obra desde el panel lateral para comenzar.")
