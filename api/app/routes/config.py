"""Que modelo corre cada paso del pipeline. Editable en caliente.

Misma forma que las politicas: el valor vigente vive en SQLite, el default en
`constants.py`, y cambiarlo es una peticion HTTP y no un deploy. El panel usa
estas rutas; `curl` tambien.

Las claves no pasan por aca. Se elige *que* modelo, nunca *con que credencial*:
una instancia publica que guardara claves ajenas seria un incidente esperando.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_modelos_service
from ..domain.models import ModeloPasoUpdate
from ..services.modelos import ModelosService

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/modelos")
def leer_modelos(modelos: ModelosService = Depends(get_modelos_service)) -> dict:
    """La configuracion vigente por paso, mas el catalogo de proveedores."""
    return {"pasos": modelos.vigente(), "proveedores": modelos.catalogo()}


@router.put("/modelos/{paso}")
def guardar_modelo(
    paso: str,
    cambio: ModeloPasoUpdate,
    modelos: ModelosService = Depends(get_modelos_service),
) -> dict:
    """Cambia el modelo de un paso. Se aplica a la siguiente investigacion."""
    try:
        modelos.guardar(paso, cambio.proveedor, cambio.modelo)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"paso": paso, **modelos.vigente()[paso]}


@router.post("/modelos/reset")
def restablecer(modelos: ModelosService = Depends(get_modelos_service)) -> dict:
    """Vuelve a los valores por defecto: borra lo guardado, no lo pisa."""
    modelos.restablecer()
    return {"pasos": modelos.vigente()}
