import os
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

# ============================================================
# Almacenamiento local (sin Firebase)
# - Obras:    data/obras/obras.json (índice) + data/obras/<codigo>.json (detalle)
# - Insumos:  data/insumos.json
# - Fotos:    data/fotos/
# ============================================================

DATA_DIR = "data"
OBRAS_DIR = os.path.join(DATA_DIR, "obras")
OBRAS_INDEX_PATH = os.path.join(OBRAS_DIR, "obras.json")
INSUMOS_PATH = os.path.join(DATA_DIR, "insumos.json")
FOTOS_DIR = os.path.join(DATA_DIR, "fotos")


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _obra_path(codigo_obra: str) -> str:
    return os.path.join(OBRAS_DIR, f"{codigo_obra}.json")


def _ensure_estructura_obra(data: Dict[str, Any]) -> Dict[str, Any]:
    # Mantener compatibilidad con versiones previas
    data = data or {}
    data.setdefault("avance", [])
    data.setdefault("presupuesto_total", 0.0)
    # Nuevo: cronograma valorizado
    data.setdefault("cronograma", [])
    # Nuevo: hitos de pago
    data.setdefault("hitos_pago", [])
    return data


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


# ==================== DIRECTORIOS ====================

def inicializar_directorios() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OBRAS_DIR, exist_ok=True)
    os.makedirs(FOTOS_DIR, exist_ok=True)

    if not os.path.exists(OBRAS_INDEX_PATH):
        _write_json(OBRAS_INDEX_PATH, {})
    if not os.path.exists(INSUMOS_PATH):
        _write_json(INSUMOS_PATH, [])


# ==================== OBRAS ====================

def cargar_obras() -> Dict[str, str]:
    return _read_json(OBRAS_INDEX_PATH, {})


def agregar_obra(codigo: str, nombre: str) -> Tuple[bool, str]:
    codigo = (codigo or "").strip()
    nombre = (nombre or "").strip()

    if not codigo:
        return False, "Código vacío."
    if not nombre:
        return False, "Nombre vacío."

    obras = cargar_obras()
    if codigo in obras:
        return False, "Ya existe una obra con ese código."

    obras[codigo] = nombre
    _write_json(OBRAS_INDEX_PATH, obras)

    # Crear archivo detalle
    datos = _ensure_estructura_obra({})
    _write_json(_obra_path(codigo), datos)

    return True, "Obra creada."


def cargar_datos_obra(codigo_obra: str) -> Dict[str, Any]:
    path = _obra_path(codigo_obra)
    data = _read_json(path, {})
    if not data:
        # Si no existe aún, crear con plantilla
        data = _ensure_estructura_obra({})
        _write_json(path, data)
    return _ensure_estructura_obra(data)


def guardar_datos_obra(codigo_obra: str, datos: Dict[str, Any]) -> None:
    _write_json(_obra_path(codigo_obra), _ensure_estructura_obra(datos))


# ==================== AVANCES ====================

def agregar_avance(codigo_obra: str, avance_dict: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        datos.setdefault("avance", [])
        datos["avance"].append(avance_dict)
        guardar_datos_obra(codigo_obra, datos)
        return True, "Avance guardado."
    except Exception as e:
        return False, str(e)


def obtener_avances_obra(codigo_obra: str) -> List[Dict[str, Any]]:
    datos = cargar_datos_obra(codigo_obra)
    avances = datos.get("avance", [])
    return avances if isinstance(avances, list) else []


# ==================== PRESUPUESTO ====================

def actualizar_presupuesto_obra(codigo_obra: str, monto: float) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        datos["presupuesto_total"] = float(monto or 0.0)
        guardar_datos_obra(codigo_obra, datos)
        return True, "Presupuesto actualizado."
    except Exception as e:
        return False, str(e)


def obtener_presupuesto_obra(codigo_obra: str) -> float:
    datos = cargar_datos_obra(codigo_obra)
    try:
        return float(datos.get("presupuesto_total", 0.0) or 0.0)
    except Exception:
        return 0.0


# ==================== INSUMOS ====================

def cargar_insumos() -> List[Dict[str, Any]]:
    insumos = _read_json(INSUMOS_PATH, [])
    return insumos if isinstance(insumos, list) else []


def guardar_insumos(insumos: List[Dict[str, Any]]) -> None:
    _write_json(INSUMOS_PATH, insumos)


def agregar_insumo(nuevo_insumo: Dict[str, Any]) -> None:
    insumos = cargar_insumos()
    insumos.append(nuevo_insumo)
    guardar_insumos(insumos)


def actualizar_insumo(indice: int, insumo_actualizado: Dict[str, Any]) -> None:
    insumos = cargar_insumos()
    if 0 <= indice < len(insumos):
        insumos[indice] = insumo_actualizado
        guardar_insumos(insumos)


def eliminar_insumo(indice: int) -> None:
    insumos = cargar_insumos()
    if 0 <= indice < len(insumos):
        insumos.pop(indice)
        guardar_insumos(insumos)


# ==================== CRONOGRAMA VALORIZADO ====================

def obtener_cronograma_obra(codigo_obra: str) -> List[Dict[str, Any]]:
    datos = cargar_datos_obra(codigo_obra)
    cronograma = datos.get("cronograma", [])
    return cronograma if isinstance(cronograma, list) else []


def agregar_partida_cronograma(codigo_obra: str, partida: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        datos.setdefault("cronograma", [])
        partida = dict(partida or {})
        partida.setdefault("id", _new_id("crono"))
        datos["cronograma"].append(partida)
        guardar_datos_obra(codigo_obra, datos)
        return True, "Partida agregada."
    except Exception as e:
        return False, str(e)


def actualizar_partida_cronograma(codigo_obra: str, partida_id: str, data_upd: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        cronograma = datos.get("cronograma", [])
        if not isinstance(cronograma, list):
            cronograma = []
        found = False
        for i, it in enumerate(cronograma):
            if isinstance(it, dict) and it.get("id") == partida_id:
                nuevo = dict(it)
                nuevo.update(data_upd or {})
                cronograma[i] = nuevo
                found = True
                break
        if not found:
            return False, "No se encontró la partida."
        datos["cronograma"] = cronograma
        guardar_datos_obra(codigo_obra, datos)
        return True, "Partida actualizada."
    except Exception as e:
        return False, str(e)


def eliminar_partida_cronograma(codigo_obra: str, partida_id: str) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        cronograma = datos.get("cronograma", [])
        if not isinstance(cronograma, list):
            cronograma = []
        nuevo = [it for it in cronograma if not (isinstance(it, dict) and it.get("id") == partida_id)]
        datos["cronograma"] = nuevo
        guardar_datos_obra(codigo_obra, datos)
        return True, "Partida eliminada."
    except Exception as e:
        return False, str(e)


# ==================== HITOS DE PAGO ====================

def obtener_hitos_pago_obra(codigo_obra: str) -> List[Dict[str, Any]]:
    datos = cargar_datos_obra(codigo_obra)
    hitos = datos.get("hitos_pago", [])
    return hitos if isinstance(hitos, list) else []


def agregar_hito_pago(codigo_obra: str, hito: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        datos.setdefault("hitos_pago", [])
        hito = dict(hito or {})
        hito.setdefault("id", _new_id("hito"))
        datos["hitos_pago"].append(hito)
        guardar_datos_obra(codigo_obra, datos)
        return True, "Hito agregado."
    except Exception as e:
        return False, str(e)


def actualizar_hito_pago(codigo_obra: str, hito_id: str, data_upd: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        hitos = datos.get("hitos_pago", [])
        if not isinstance(hitos, list):
            hitos = []
        found = False
        for i, it in enumerate(hitos):
            if isinstance(it, dict) and it.get("id") == hito_id:
                nuevo = dict(it)
                nuevo.update(data_upd or {})
                hitos[i] = nuevo
                found = True
                break
        if not found:
            return False, "No se encontró el hito."
        datos["hitos_pago"] = hitos
        guardar_datos_obra(codigo_obra, datos)
        return True, "Hito actualizado."
    except Exception as e:
        return False, str(e)


def eliminar_hito_pago(codigo_obra: str, hito_id: str) -> Tuple[bool, str]:
    try:
        datos = cargar_datos_obra(codigo_obra)
        hitos = datos.get("hitos_pago", [])
        if not isinstance(hitos, list):
            hitos = []
        nuevo = [it for it in hitos if not (isinstance(it, dict) and it.get("id") == hito_id)]
        datos["hitos_pago"] = nuevo
        guardar_datos_obra(codigo_obra, datos)
        return True, "Hito eliminado."
    except Exception as e:
        return False, str(e)
