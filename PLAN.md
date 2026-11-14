# Plan del CLI de Mortis Engine

El CLI instalable ejecuta el análisis sobre Git local usando la conexión guardada. El README documenta los comandos y las opciones de configuración.

## Repositorios

1. **mortis-engine** contiene el CLI, los transportes de IA, la revisión, la evaluación de candidatos, la verificación, los resúmenes, el historial y los scans con Trivy. La librería y el CLI devuelven informes JSON versionados.
2. **Code Mortis app** podrá consumir esos contratos y encargarse de la autorización de GitHub, la programación de trabajos y la publicación. La adopción requiere comparar resultados sobre entradas idénticas.
3. **La GitHub Action** podrá instalar una versión fijada del CLI, revisar el checkout y convertir los hallazgos confirmados en comentarios del PR. El publicador deberá respetar los SHA y la cobertura del informe.

Las pruebas del CLI comprueban contratos, cobertura, límites y manejo de fallos. La evaluación comparativa de calidad con proveedores reales requiere una ejecución independiente.
