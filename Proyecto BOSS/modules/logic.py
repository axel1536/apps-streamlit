from __future__ import annotations

from datetime import datetime, date
import os
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd

from modules.database import obtener_avances_obra, cargar_insumos

# Base de jornada estándar para rendimiento por día
HORAS_DIA_ESTANDAR = 8

# ==================== VALIDACIONES ====================

def validar_insumo(nombre: str, unidad: str, precio: Any) -> Tuple[bool, str]:
    if not nombre or not str(nombre).strip():
        return False, "El nombre del insumo es requerido"
    if not unidad or not str(unidad).strip():
        return False, "La unidad es requerida"
    try:
        precio_float = float(precio)
        if precio_float < 0:
            return False, "El precio no puede ser negativo"
    except (ValueError, TypeError):
        return False, "El precio debe ser un número válido"
    return True, ""


def validar_obra(codigo: str, nombre: str, obras_existentes: Optional[dict] = None) -> Tuple[bool, str]:
    if not codigo or not codigo.strip():
        return False, "El código de la obra es requerido"
    if not nombre or not nombre.strip():
        return False, "El nombre de la obra es requerido"

    codigo = codigo.strip()
    nombre = nombre.strip()

    if not all(c.isalnum() or c in ["-", "_"] for c in codigo):
        return False, "El código solo debe contener letras, números, guiones o guiones bajos"
    if len(codigo) < 3:
        return False, "El código debe tener al menos 3 caracteres"
    if len(nombre) < 5:
        return False, "El nombre debe tener al menos 5 caracteres"
    if obras_existentes and codigo in obras_existentes:
        return False, "Ya existe una obra con ese código"
    return True, ""


def validar_insumo_duplicado(nombre: str, insumos_existentes: List[Dict[str, Any]]) -> bool:
    nombre_lower = (nombre or "").strip().lower()
    for insumo in insumos_existentes or []:
        if str(insumo.get("Insumo", "")).strip().lower() == nombre_lower:
            return True
    return False


def validar_cantidad_positiva(cantidad: Any, nombre_campo: str = "cantidad") -> Tuple[bool, str]:
    try:
        cant = float(cantidad)
        if cant <= 0:
            return False, f"La {nombre_campo} debe ser mayor a 0"
        return True, ""
    except (ValueError, TypeError):
        return False, f"La {nombre_campo} debe ser un número válido"


def validar_costos_parte_diario(insumos_mo, insumos_mat, insumos_eq, insumos_otros) -> Tuple[bool, str]:
    total_items = len(insumos_mo) + len(insumos_mat) + len(insumos_eq) + len(insumos_otros)
    if total_items == 0:
        return False, "Debes agregar al menos un insumo en alguna categoría de costos"
    return True, ""


def validar_parte_diario_completo(responsable: str, avance: float, rendimiento: float, unidad: str, fotos: list) -> Tuple[bool, List[str]]:
    errores: List[str] = []
    if not responsable or not responsable.strip():
        errores.append("Debes especificar el responsable")
    if avance <= 0:
        errores.append("El avance debe ser mayor a 0%")
    if rendimiento <= 0:
        errores.append("El rendimiento de la partida debe ser mayor a 0")
    if not unidad or not unidad.strip():
        errores.append("Debes especificar la unidad de medida")
    if len(fotos) < 3:
        errores.append("Debes subir mínimo 3 fotos")
    return len(errores) == 0, errores


def validar_extension_archivo(nombre_archivo: str, extensiones_permitidas: List[str] = [".jpg", ".jpeg", ".png"]) -> bool:
    _, ext = os.path.splitext(str(nombre_archivo).lower())
    return ext in extensiones_permitidas


def validar_partida_cronograma(nombre: str, fecha_inicio: date, fecha_fin: date, monto_planificado: Any) -> Tuple[bool, str]:
    if not nombre or not str(nombre).strip():
        return False, "Debes ingresar el nombre de la partida"
    if not isinstance(fecha_inicio, date) or not isinstance(fecha_fin, date):
        return False, "Fechas inválidas"
    if fecha_fin < fecha_inicio:
        return False, "La fecha fin no puede ser anterior a la fecha inicio"
    try:
        monto = float(monto_planificado)
        if monto <= 0:
            return False, "El monto planificado debe ser mayor a 0"
    except (ValueError, TypeError):
        return False, "El monto planificado debe ser un número válido"
    return True, ""


def validar_hito_pago(descripcion: str, fecha: date, monto: Any) -> Tuple[bool, str]:
    if not descripcion or not str(descripcion).strip():
        return False, "Debes ingresar la descripción del hito"
    if not isinstance(fecha, date):
        return False, "Fecha inválida"
    try:
        monto_f = float(monto)
        if monto_f <= 0:
            return False, "El monto del hito debe ser mayor a 0"
    except (ValueError, TypeError):
        return False, "El monto del hito debe ser un número válido"
    return True, ""


# ==================== PROCESAMIENTO: FOTOS Y AVANCES ====================

def guardar_fotos_avance(codigo_obra: str, fotos, fecha_hoy: date) -> List[str]:
    rutas_fotos: List[str] = []
    os.makedirs("data/fotos", exist_ok=True)

    for f in fotos:
        if not validar_extension_archivo(getattr(f, "name", "")):
            continue

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:20]
        nombre_archivo = f"{codigo_obra}_{fecha_hoy}_{timestamp}_{f.name}"
        ruta = f"data/fotos/{nombre_archivo}"

        with open(ruta, "wb") as file:
            file.write(f.getbuffer())

        rutas_fotos.append(ruta)

    return rutas_fotos


def crear_avance_dict(
    fecha: date,
    responsable: str,
    avance_pct: float,
    observaciones: str,
    rutas_fotos: List[str],
    nombre_partida: str = "",
    rendimiento_partida: float = 0,
    unidad_medida: str = "",
    horas_mano_obra: float = 0,
    cantidad_ejecutada: float = 0,
    insumos_mo: Optional[list] = None,
    insumos_mat: Optional[list] = None,
    insumos_eq: Optional[list] = None,
    insumos_otros: Optional[list] = None,
    totales: Optional[dict] = None,
) -> Dict[str, Any]:
    return {
        "fecha": str(fecha),
        "responsable": responsable,
        "avance": avance_pct,
        "obs": observaciones,
        "fotos": rutas_fotos,
        "partida": {
            "nombre": nombre_partida,
            "rendimiento": rendimiento_partida,
            "unidad": unidad_medida,
            "jornal_horas": horas_mano_obra,
            "cantidad_ejecutada": cantidad_ejecutada,
        },
        "costos": {
            "mano_de_obra": insumos_mo or [],
            "materiales": insumos_mat or [],
            "equipos": insumos_eq or [],
            "otros": insumos_otros or [],
        },
        "totales": totales
        or {
            "mano_de_obra": 0,
            "materiales": 0,
            "equipos": 0,
            "otros": 0,
            "total_general": 0,
            "total_general_ejecutado": 0,
        },
    }


def preparar_historial_avances(codigo_obra: str) -> List[Dict[str, Any]]:
    avances = obtener_avances_obra(codigo_obra)
    if not avances:
        return []

    df = pd.DataFrame(avances)
    if df.empty:
        return []

    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.sort_values(by="fecha", ascending=False)

    items = []
    for _, row in df.iterrows():
        fecha_fmt = row["fecha"].strftime("%d/%m/%Y") if not pd.isna(row["fecha"]) else "Fecha no disponible"
        partida = row.get("partida", {}) if isinstance(row.get("partida", {}), dict) else {}
        costos = row.get("costos", {}) if isinstance(row.get("costos", {}), dict) else {}
        totales = row.get("totales", {}) if isinstance(row.get("totales", {}), dict) else {}

        items.append(
            {
                "fecha_fmt": fecha_fmt,
                "responsable": row.get("responsable", "—"),
                "avance_pct": row.get("avance", 0),
                "obs": row.get("obs", ""),
                "fotos": row.get("fotos", []),
                "partida": partida,
                "costos": costos,
                "totales": totales,
            }
        )
    return items


def preparar_tabla_insumos():
    return cargar_insumos()


# ==================== COSTOS ====================

def calcular_cantidad_hh(cuadrilla: float, jornal_horas: float, rendimiento: float) -> float:
    # HH por unidad (asumiendo rendimiento en unidad/día)
    if rendimiento > 0:
        return (cuadrilla * float(jornal_horas)) / float(rendimiento)
    return 0.0


def calcular_parcial(cantidad: float, precio_unitario: float) -> float:
    return float(cantidad) * float(precio_unitario)


def obtener_precio_insumo(insumos_lista: List[Dict[str, Any]], nombre_insumo: str) -> float:
    for i in insumos_lista or []:
        if i.get("Insumo") == nombre_insumo:
            try:
                return float(i.get("Precio Unitario", 0.0) or 0.0)
            except Exception:
                return 0.0
    return 0.0


def _sum_parcial(items: Optional[List[Dict[str, Any]]]) -> float:
    total = 0.0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        val = it.get("Parcial (S/)")
        if val is None:
            val = it.get("Parcial (S/.)")
        if val is None:
            val = it.get("Parcial (S/)")
        if val is None:
            val = it.get("Parcial")
        try:
            total += float(val or 0.0)
        except Exception:
            total += 0.0
    return total


def calcular_totales_costos(insumos_mo, insumos_mat, insumos_eq, insumos_otros, cantidad_ejecutada: float = 1) -> Dict[str, float]:
    total_mo = _sum_parcial(insumos_mo)
    total_mat = _sum_parcial(insumos_mat)
    total_eq = _sum_parcial(insumos_eq)
    total_otros = _sum_parcial(insumos_otros)

    total_general = total_mo + total_mat + total_eq + total_otros
    cantidad_ejecutada = float(cantidad_ejecutada or 0.0)
    total_general_ejecutado = total_general * cantidad_ejecutada if cantidad_ejecutada > 0 else 0.0

    return {
        "mano_de_obra": total_mo,
        "materiales": total_mat,
        "equipos": total_eq,
        "otros": total_otros,
        "total_general": total_general,
        "total_general_ejecutado": total_general_ejecutado,
    }


# ==================== PRESUPUESTO ====================

def calcular_gastos_acumulados(avances: List[Dict[str, Any]]) -> float:
    total_gastado = 0.0
    for avance in avances or []:
        totales = avance.get("totales", {}) if isinstance(avance.get("totales", {}), dict) else {}
        total_ejecutado = totales.get("total_general_ejecutado", 0) or 0
        if not total_ejecutado:
            total_ejecutado = totales.get("total_general", 0) or 0
        try:
            total_gastado += float(total_ejecutado)
        except Exception:
            pass
    return total_gastado


def calcular_resumen_presupuesto(presupuesto_total: Any, avances: List[Dict[str, Any]]) -> Dict[str, float]:
    presupuestado = float(presupuesto_total) if presupuesto_total else 0.0
    gastado = calcular_gastos_acumulados(avances)
    disponible = presupuestado - gastado
    porcentaje_gastado = (gastado / presupuestado * 100) if presupuestado > 0 else 0.0

    return {
        "presupuestado": presupuestado,
        "gastado": gastado,
        "disponible": disponible,
        "porcentaje_gastado": porcentaje_gastado,
    }


# ==================== RENDIMIENTO ====================

def calcular_eficiencia_rendimiento(cantidad_ejecutada: float, rendimiento_esperado: float, horas_trabajadas: float) -> float:
    if rendimiento_esperado <= 0 or horas_trabajadas <= 0:
        return 0.0
    produccion_esperada = float(rendimiento_esperado) * (float(horas_trabajadas) / HORAS_DIA_ESTANDAR)
    if produccion_esperada == 0:
        return 0.0
    return (float(cantidad_ejecutada) / produccion_esperada) * 100.0


def obtener_estado_rendimiento(eficiencia: float) -> Tuple[str, str, str]:
    if eficiencia >= 100:
        return "🟢", "Excelente", "normal"
    if eficiencia >= 80:
        return "🟡", "Aceptable", "normal"
    return "🔴", "Crítico", "inverse"


def calcular_eficiencia_promedio_obra(avances: List[Dict[str, Any]]) -> float:
    eficiencias: List[float] = []
    for avance in avances or []:
        partida = avance.get("partida", {})
        if not isinstance(partida, dict):
            continue
        cantidad_ejecutada = partida.get("cantidad_ejecutada", 0) or 0
        rendimiento = partida.get("rendimiento", 0) or 0
        horas = partida.get("jornal_horas", 0) or 0
        if rendimiento > 0 and horas > 0 and cantidad_ejecutada > 0:
            eficiencias.append(calcular_eficiencia_rendimiento(cantidad_ejecutada, rendimiento, horas))
    if not eficiencias:
        return 0.0
    return sum(eficiencias) / len(eficiencias)


# ==================== CRONOGRAMA VALORIZADO (Curva S) ====================

def _parse_date_any(x: Any) -> Optional[pd.Timestamp]:
    if x is None:
        return None
    try:
        return pd.to_datetime(x, errors="coerce")
    except Exception:
        return None


def construir_curva_s_planificada(cronograma: List[Dict[str, Any]], freq: str = "Semanal") -> pd.DataFrame:
    """Devuelve DataFrame con columnas: fecha, plan_dia, plan_acum"""
    if not cronograma:
        return pd.DataFrame(columns=["fecha", "plan_dia", "plan_acum"])

    rows = []
    for it in cronograma:
        if not isinstance(it, dict):
            continue
        fi = _parse_date_any(it.get("fecha_inicio"))
        ff = _parse_date_any(it.get("fecha_fin"))
        try:
            monto = float(it.get("monto_planificado", 0) or 0)
        except Exception:
            monto = 0.0

        if fi is None or ff is None or pd.isna(fi) or pd.isna(ff) or monto <= 0:
            continue

        fi = fi.normalize()
        ff = ff.normalize()
        if ff < fi:
            continue

        dias = int((ff - fi).days) + 1
        if dias <= 0:
            continue

        diario = monto / dias
        for d in pd.date_range(fi, ff, freq="D"):
            rows.append({"fecha": d, "plan_dia": diario})

    if not rows:
        return pd.DataFrame(columns=["fecha", "plan_dia", "plan_acum"])

    df = pd.DataFrame(rows).groupby("fecha", as_index=False)["plan_dia"].sum()
    df = df.sort_values("fecha")

    # Agrupar según frecuencia para visualización
    if freq.lower().startswith("mens"):
        df["bucket"] = df["fecha"].dt.to_period("M").dt.to_timestamp()
    elif freq.lower().startswith("dia"):
        df["bucket"] = df["fecha"]
    else:
        # Semanal (inicio de semana lunes)
        df["bucket"] = df["fecha"].dt.to_period("W-MON").dt.start_time

    df = df.groupby("bucket", as_index=False)["plan_dia"].sum().rename(columns={"bucket": "fecha"})
    df = df.sort_values("fecha")
    df["plan_acum"] = df["plan_dia"].cumsum()
    return df


def construir_curva_s_real(avances: List[Dict[str, Any]], freq: str = "Semanal") -> pd.DataFrame:
    """Devuelve DataFrame con columnas: fecha, real_dia, real_acum"""
    if not avances:
        return pd.DataFrame(columns=["fecha", "real_dia", "real_acum"])

    rows = []
    for av in avances:
        if not isinstance(av, dict):
            continue
        f = _parse_date_any(av.get("fecha"))
        if f is None or pd.isna(f):
            continue
        f = f.normalize()

        tot = av.get("totales", {}) if isinstance(av.get("totales", {}), dict) else {}
        costo = tot.get("total_general_ejecutado", 0) or tot.get("total_general", 0) or 0
        try:
            costo = float(costo)
        except Exception:
            costo = 0.0

        if costo <= 0:
            continue

        rows.append({"fecha": f, "real_dia": costo})

    if not rows:
        return pd.DataFrame(columns=["fecha", "real_dia", "real_acum"])

    df = pd.DataFrame(rows).groupby("fecha", as_index=False)["real_dia"].sum()
    df = df.sort_values("fecha")

    if freq.lower().startswith("mens"):
        df["bucket"] = df["fecha"].dt.to_period("M").dt.to_timestamp()
    elif freq.lower().startswith("dia"):
        df["bucket"] = df["fecha"]
    else:
        df["bucket"] = df["fecha"].dt.to_period("W-MON").dt.start_time

    df = df.groupby("bucket", as_index=False)["real_dia"].sum().rename(columns={"bucket": "fecha"})
    df = df.sort_values("fecha")
    df["real_acum"] = df["real_dia"].cumsum()
    return df


def calcular_resumen_cronograma(cronograma: List[Dict[str, Any]], avances: List[Dict[str, Any]], fecha_corte: Optional[date] = None) -> Dict[str, float]:
    """Indicadores básicos tipo Curva-S (PV vs AC)"""
    fecha_corte = fecha_corte or date.today()
    plan_df = construir_curva_s_planificada(cronograma, freq="Diario")
    real_df = construir_curva_s_real(avances, freq="Diario")

    pv_total = float(plan_df["plan_dia"].sum()) if not plan_df.empty else 0.0
    ac_total = float(real_df["real_dia"].sum()) if not real_df.empty else 0.0

    pv_to_date = float(plan_df.loc[plan_df["fecha"].dt.date <= fecha_corte, "plan_dia"].sum()) if not plan_df.empty else 0.0
    ac_to_date = float(real_df.loc[real_df["fecha"].dt.date <= fecha_corte, "real_dia"].sum()) if not real_df.empty else 0.0

    # Variación (positiva = sobrecosto vs plan a la fecha)
    sv = ac_to_date - pv_to_date
    spi = (ac_to_date / pv_to_date) if pv_to_date > 0 else 0.0

    return {
        "pv_total": pv_total,
        "ac_total": ac_total,
        "pv_to_date": pv_to_date,
        "ac_to_date": ac_to_date,
        "sv": sv,
        "spi": spi,
    }


def construir_tabla_curvas(plan_df: pd.DataFrame, real_df: pd.DataFrame) -> pd.DataFrame:
    """Une Plan (PV) y Real (AC) en una sola tabla por fecha.

    Espera:
      - plan_df: columnas [fecha, plan_dia] y opcionalmente [plan_acum]
      - real_df: columnas [fecha, real_dia] y opcionalmente [real_acum]

    Retorna:
      DataFrame con columnas: [fecha, plan_dia, plan_acum, real_dia, real_acum]
    """

    # Normalizar entradas vacías
    if plan_df is None or len(plan_df) == 0:
        plan_df = pd.DataFrame(columns=["fecha", "plan_dia", "plan_acum"])
    if real_df is None or len(real_df) == 0:
        real_df = pd.DataFrame(columns=["fecha", "real_dia", "real_acum"])

    # Copias defensivas
    p = plan_df.copy()
    r = real_df.copy()

    # Asegurar columnas mínimas
    if "fecha" not in p.columns:
        p["fecha"] = pd.NaT
    if "plan_dia" not in p.columns:
        p["plan_dia"] = 0.0

    if "fecha" not in r.columns:
        r["fecha"] = pd.NaT
    if "real_dia" not in r.columns:
        r["real_dia"] = 0.0

    # Normalizar fechas
    p["fecha"] = pd.to_datetime(p["fecha"], errors="coerce")
    r["fecha"] = pd.to_datetime(r["fecha"], errors="coerce")

    # Consolidar por fecha (por si llegan repetidas)
    p = p.dropna(subset=["fecha"]).groupby("fecha", as_index=False)["plan_dia"].sum()
    r = r.dropna(subset=["fecha"]).groupby("fecha", as_index=False)["real_dia"].sum()

    # Merge outer para tener toda la línea de tiempo
    tabla = pd.merge(p, r, on="fecha", how="outer").fillna({"plan_dia": 0.0, "real_dia": 0.0})
    tabla = tabla.sort_values("fecha")

    # Acumulados
    tabla["plan_acum"] = tabla["plan_dia"].cumsum()
    tabla["real_acum"] = tabla["real_dia"].cumsum()

    # Tipos numéricos
    for c in ["plan_dia", "plan_acum", "real_dia", "real_acum"]:
        tabla[c] = pd.to_numeric(tabla[c], errors="coerce").fillna(0.0)

    return tabla.reset_index(drop=True)


def calcular_resumen_hitos(hitos: List[Dict[str, Any]]) -> Dict[str, float]:
    """Resumen simple de hitos de pago.

    Se asume que cada hito tiene al menos:
      - monto (numérico)
      - estado: 'Pendiente' o 'Pagado' (no sensible a mayúsc/minúsc)
    """
    total = 0.0
    pagado = 0.0
    pendiente = 0.0

    for h in hitos or []:
        if not isinstance(h, dict):
            continue
        try:
            monto = float(h.get("monto", 0) or 0)
        except Exception:
            monto = 0.0

        if monto <= 0:
            continue

        total += monto

        estado = str(h.get("estado", "Pendiente") or "Pendiente").strip().lower()
        if estado in {"pagado", "pagada", "paid"}:
            pagado += monto
        else:
            # Cualquier cosa diferente se considera pendiente
            pendiente += monto

    return {
        "total_hitos": total,
        "pagado": pagado,
        "pendiente": pendiente,
    }
