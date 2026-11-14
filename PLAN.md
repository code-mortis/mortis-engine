# Plan del CLI de Mortis Engine

**Primera entrega:** instalar `mortis`, conectarlo a la IA y revisar dos commits del Git local. Si el proveedor es remoto, recibe el contexto de código necesario. El CLI funciona sin GitHub ni una instancia de Code Mortis.

## 1. Instalar y conectar

- Crear un paquete Python, `mortis-engine`, que exponga la librería `mortis_engine` y el comando `mortis`. Instalarlo desde su carpeta con `uv tool install --editable .` y, una vez publicado, con `uv tool install mortis-engine`.
- Implementar `mortis connect` para crear y probar perfiles. Reutilizar de Code Mortis los esquemas de inferencia y los transportes HTTP y Bedrock. Conservar OpenAI Chat Completions, OpenAI Responses, Anthropic y Bedrock.
- Guardar perfiles en `~/.config/mortis/config.toml`: proveedor, protocolo, modelo, URL, timeout, límite de tokens por petición, códigos de reintento, cuerpo extra, cabeceras y opciones de AWS. Las claves y cabeceras sensibles se leen del llavero o del entorno. Bedrock también admite las credenciales de AWS.

**Listo cuando** el CLI instalado prueba una conexión real desde cualquier carpeta. Un fallo de autenticación identifica la conexión sin cambiar de proveedor.

## 2. Revisar dos commits locales

- Implementar `mortis review --base main --head HEAD`. Calcular el merge base y reutilizar, en orden, la selección de archivos y reglas de Code Mortis, la preparación de peticiones y la validación de hallazgos y cobertura. Los puntos de partida son `core/file_policy.py`, `reviews/planning.py` y `reviews/engine.py` del backend actual.
- Leer la configuración y las reglas de `mortis.toml` desde el commit base. Reutilizar los nombres, valores por defecto y validaciones de `RepoSettings`: modelo, idioma, esfuerzo, sensibilidad, contexto, exclusiones, reglas, máximo de archivos, concurrencia, tiempos y presupuestos por etapa. `mortis config show` muestra los valores efectivos sin secretos. Si se pide una función pendiente, como verificar hallazgos históricos, indicarlo antes de consumir tokens.
- Devolver desde la librería un resultado que el CLI presenta como texto o JSON versionado: commits, hallazgos, evidencia, líneas, cobertura, errores y consumo. La futura app podrá importar esa función y la Action podrá leer el JSON.

**Listo para la primera versión cuando** el CLI y el análisis inicial de Code Mortis coinciden en hallazgos, líneas y cobertura con los mismos commits, configuración, contexto y respuestas grabadas. Incluir un archivo parcialmente revisado y comprobar además una review corta con una conexión real.

## 3. Añadir seguridad después de la primera versión

Implementar `mortis scan --ref HEAD` sobre el Git local. Reutilizar primero Trivy y su normalización en `security_scans/engine.py`; después, el análisis de seguridad con IA sobre la conexión existente. Conservar los ajustes de modelo, concurrencia, timeout y presupuesto de `SecurityScanInput`.

**Listo cuando** un scan de prueba detecta un hallazgo conocido y, si falla Trivy o la IA, el informe identifica qué etapa terminó y qué cobertura falta.

La publicación de comentarios, los eventos de GitHub y la integración con los workers se planifican después, en sus respectivos repositorios.
