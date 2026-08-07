import pathlib

from . import v1_judge, v1_policy_eval, v1_resolution

__all__ = ["v1_judge", "v1_policy_eval", "v1_resolution"]


def versiones() -> dict[str, str]:
    """La version vigente de cada prompt, leida de su changelog.

    El informe declaraba «v1_policy_eval, v1_resolution, v1_judge» como texto
    fijo en la plantilla. Eso es el nombre del MODULO, no la version: los
    prompts iban por v1.2, v3.1 y v2.1. La documentacion afirma que «el prefijo
    de version hace explicito que version de prompt produjo que resolucion en el
    audit trail», y el audit trail decia v1 para un prompt v3.1.

    La primera linea de cada modulo es su entrada de changelog mas reciente, y
    de ahi sale el numero: una sola fuente, la que se edita al cambiar el prompt.
    """
    import re

    salida: dict[str, str] = {}
    for modulo in (v1_policy_eval, v1_resolution, v1_judge):
        ruta = pathlib.Path(modulo.__file__)
        primera = ruta.read_text(encoding="utf-8").splitlines()[0]
        m = re.search(r"PROMPT VERSION:\s*(v[\d.]+)", primera)
        salida[ruta.stem] = m.group(1) if m else "?"
    return salida
