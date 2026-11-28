"""La pantalla de informes (`/informes/`) y su entrada en el menú principal.

    C:\\Python314\\python.exe -m unittest tests.test_informes_pagina -v
"""
import json
import os
import re
import shutil
import subprocess
import unittest
from unittest import mock

import app
import auth


def cliente(rol=None):
    c = app.app.test_client()
    if rol:
        with c.session_transaction() as s:
            s["rol"], s["usuario"] = rol, "x"
    return c


class TestPagina(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa")
        p.start()
        self.addCleanup(p.stop)

    def test_exige_ser_administrador(self):
        self.assertIn(cliente().get("/informes/").status_code, (302, 401))
        self.assertEqual(cliente("operario").get("/informes/").status_code, 403)

    def test_el_administrador_la_ve_y_trae_el_tambo(self):
        r = cliente("admin").get("/informes/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        html = r.get_data(as_text=True)
        self.assertIn("/api/informes/campos", html)
        self.assertIn('"ponderosa"', html)

    def test_embebida_en_el_menu_no_repite_el_encabezado(self):
        normal = cliente("admin").get("/informes/").get_data(as_text=True)
        emb = cliente("admin").get("/informes/?embebido=1").get_data(as_text=True)
        self.assertIn("const EMBEBIDO = true", emb)
        self.assertIn("const EMBEBIDO = false", normal)

    def test_ninguna_marca_de_jinja_se_cuela_en_el_codigo(self):
        """`{{` o `{%` dentro del JavaScript de una plantilla la rompe con un 500 (ver CLAUDE.md)."""
        with open("templates/informes.html", encoding="utf-8") as f:
            fuente = f.read()
        import re
        marcas = re.findall(r"\{\{.*?\}\}|\{%.*?%\}", fuente)
        self.assertEqual(sorted(set(marcas)), sorted({"{{ tambo|tojson }}", "{{ nombre_tambo|tojson }}", "{{ embebido|tojson }}"}))


def sin_manejador(fuente: str) -> list:
    """Los `data-act` y `data-chg` que la pantalla genera y ningún manejador atiende (`a === '...'`)."""
    pedidas = set(re.findall(r'data-(?:act|chg)="([A-Za-z0-9]+)"', fuente))
    manejadas = set(re.findall(r"a === '([A-Za-z0-9]+)'", fuente))
    return sorted(pedidas - manejadas)


class TestFase2EnLaPantalla(unittest.TestCase):
    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_cada_boton_y_cada_selector_tiene_su_manejador(self):
        """Un `data-act` sin manejador es un botón que no hace nada y no da error en ningún lado."""
        self.assertEqual(sin_manejador(self.fuente()), [])
        # Los dos niveles de agrupación se arman con data-chg="g' + n + '": no los ve la búsqueda de arriba.
        self.assertTrue({"g1", "g2"} <= set(re.findall(r"a === '([A-Za-z0-9]+)'", self.fuente())))

    def test_el_control_detecta_un_manejador_que_falta(self):
        falso = "<button data-act=\"nuevo\"></button><select data-chg=\"rs\"></select> if (a === 'rs') {}"
        self.assertEqual(sin_manejador(falso), ["nuevo"])

    def test_trae_la_barra_de_agrupacion_y_el_cuadro_del_campo_calculado(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="groupbar"', '<dialog id="calc"', 'id="c-formula"', "/api/informes/formula",
                         'data-act="plegar"', 'data-act="nuevocalc"', 'data-chg="rs"', 'data-chg="solo"'):
            self.assertIn(esperado, html, esperado)

    def test_los_textos_que_escribe_el_usuario_pasan_por_esc(self):
        """El nombre de un campo calculado y la clave de un grupo se escriben a mano o vienen de la base: nunca van crudos al HTML."""
        fuente = self.fuente()
        for pieza in ("esc(f.etiqueta)", "esc(et.length", "esc(meta.etiqueta)", "esc(textoClave(", "esc(g.etiqueta)", "esc(c.etiqueta)"):
            self.assertIn(pieza, fuente, pieza)
        self.assertNotIn(".innerHTML = e.message", fuente)          # los mensajes de error van con textContent
        self.assertIn("e.textContent = texto", fuente)


class TestFase3EnLaPantalla(unittest.TestCase):
    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_trae_la_pestana_del_grafico_y_sus_controles(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="pg-graf"', 'id="btn-graficar"', 'id="chart"', 'id="stats"', 'id="lg"', 'data-act="gseg"', 'data-chg="gs"',
                         'data-chg="gsolo"', 'data-chg="gd"', "/api/informes/serie", 'src="/static/js/informes_grafico.js"'):
            self.assertTrue(esperado in html, esperado)

    def test_lo_que_viene_del_servidor_pasa_por_esc_en_el_grafico(self):
        """Los nombres de rodeo vienen de la base y el mensaje de error del servidor: nunca van crudos al HTML."""
        fuente = self.fuente()
        for pieza in ("esc(s.etiqueta)", "esc(GRAF.etiquetaTip(", "esc(d.serieError)", "esc(d.nombre"):
            self.assertTrue(pieza in fuente, pieza)

    def test_el_grafico_se_guarda_con_el_informe_pero_no_ensucia_la_tabla(self):
        fuente = self.fuente()
        self.assertTrue("d.grafico ? {grafico: d.grafico} : {}" in fuente)           # se manda a guardar solo si se configuró
        self.assertTrue("delete def.grafico;" in fuente)                              # al abrir, sale de la definición de la tabla
        self.assertTrue("[d.nombre, d.def, d.grafico]" in fuente)                     # y un cambio de gráfico cuenta como «sin guardar»

    def test_la_serie_se_pide_con_la_definicion_del_informe_y_la_configuracion_del_grafico(self):
        fuente = self.fuente()
        self.assertTrue("post('/api/informes/serie', Object.assign(limpiaDef(d.def), {grafico: cfgGraf(d)}" in fuente)


class TestFase4EnLaPantalla(unittest.TestCase):
    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_trae_el_cuadro_para_traer_un_informe_de_delpro(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="btn-delpro"', '<dialog id="delpro"', 'id="dp-lista"', 'id="dp-q"', 'id="dp-tambo"', 'id="dp-falta"', 'id="importado"',
                         "/api/informes/delpro"):
            self.assertIn(esperado, html, esperado)

    def test_los_botones_nuevos_tienen_su_manejador(self):
        fuente = self.fuente()
        manejadas = set(re.findall(r"a === '([A-Za-z0-9]+)'", fuente))
        self.assertTrue({"dptraer", "cerrarimp"} <= manejadas)

    def test_lo_que_viene_de_delpro_pasa_por_esc(self):
        """Los nombres de informe, carpetas, campos y avisos son datos de DelPro (o frases armadas con ellos): nunca van crudos al HTML."""
        fuente = self.fuente()
        for pieza in ("esc(i.nombre)", "esc(sub.filter(Boolean)", "[i.carpeta, i.motivo]", "esc(f.campo)", "esc(im.titulo)", "esc(im.sub || '')", "esc(chip[0])",
                      "esc(x.titulo)", "esc(x.clase)", "esc(clase)", "esc(x.texto)", "esc(s)", "esc(f.nombre)", "esc(im.descripcion)", "esc(im.vacio || '')", "esc(DP.error)",
                      "esc(j ? j.motivo", "esc(j.motivo)"):
            self.assertTrue(pieza in fuente, pieza)               # (assertTrue: si falla, no vuelca el archivo entero en el mensaje)

    def test_el_informe_traido_se_abre_sin_guardar(self):
        """No se pasa `guardado`: la pestaña queda con el punto de «sin guardar» hasta que el usuario lo revise y lo guarde."""
        fuente = self.fuente()
        bloque = fuente[fuente.index("function abrirComoNuevo"):fuente.index("async function traerDeDelpro")]
        self.assertNotIn("guardado", bloque)
        self.assertIn("nuevoDoc({nombre:", bloque)
        self.assertIn("importado: Object.assign({cerrado: false}, origen)", bloque)

    def test_el_informe_traido_de_delpro_se_ejecuta_y_el_armado_con_ia_no(self):
        """Lo de DelPro es un informe que el usuario ya conoce; lo de la IA es una interpretación: se revisa antes de gastar una consulta."""
        fuente = self.fuente()
        delpro = fuente[fuente.index("async function traerDeDelpro"):fuente.index("function renderImportado")]
        ia = fuente[fuente.index("async function armarConFrase"):fuente.index("/* ---------- gráfico (Fase 3)")]
        self.assertIn("ejecutar(d)", delpro)
        self.assertNotIn("ejecutar(", ia)

    def test_trae_el_cuadro_para_describir_el_informe(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="ask"', 'id="ask-t"', 'id="ask-ir"', 'id="ask-st"', "/api/informes/describir", "Describí el informe que querés"):
            self.assertIn(esperado, html, esperado)

    def test_los_mensajes_del_cuadro_van_como_texto_y_lo_de_la_ia_pasa_por_esc(self):
        fuente = self.fuente()
        self.assertIn("st.textContent = texto", fuente)                                # estadoAsk: el mensaje del servidor nunca se interpreta como HTML
        self.assertNotIn("$('#ask-st').innerHTML", fuente)
        bloque = fuente[fuente.index("async function armarConFrase"):fuente.index("/* ---------- gráfico (Fase 3)")]
        self.assertIn("abrirComoNuevo(j.nombre, j.definicion,", bloque)                # la definición entra por la misma puerta que la de DelPro
        self.assertIn("estadoAsk(e.message, true)", bloque)

    def test_sin_clave_de_la_ia_el_cuadro_avisa_y_el_resto_sigue_andando(self):
        fuente = self.fuente()
        self.assertIn("IA.configurada = !!campos.ia;", fuente)
        self.assertIn("$('#ask-t').disabled = !IA.configurada", fuente)
        self.assertIn("La IA no está configurada en este servidor", fuente)

    def test_es_uno_de_se_escribe_con_comas_y_viaja_como_lista(self):
        fuente = self.fuente()
        self.assertIn("['en', 'es uno de']", fuente)
        self.assertIn("function listaDe(", fuente)
        self.assertIn("{campo: c.campo, op: 'en', valor: l}", fuente)


class TestFase4cEnLaPantalla(unittest.TestCase):
    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_trae_el_cuadro_para_programar_el_envio(self):
        html = cliente("admin").get("/informes/").get_data(as_text=True)
        for esperado in ('id="btn-prog"', '<dialog id="prog"', 'id="prog-dias"', 'id="prog-horas"', 'id="prog-canales"', 'name="prog-fmt"', 'id="prog-guardar"',
                         'id="prog-pausa"', 'id="prog-ver"', 'id="prog-enviar"', 'id="prog-reg-cuerpo"', "/envio", "/api/informes/envios"):
            self.assertIn(esperado, html, esperado)

    def test_el_boton_nuevo_tiene_su_manejador(self):
        self.assertIn("progquitar", set(re.findall(r"a === '([A-Za-z0-9]+)'", self.fuente())))

    def test_solo_se_programa_un_informe_guardado(self):
        fuente = self.fuente()
        # Sin guardar el botón sigue activo (con aria-disabled) y al tocarlo explica por qué: un botón `disabled` no dice nada (lo ejecuta TestPantallaEjecutada).
        self.assertIn("$('#btn-prog').setAttribute('aria-disabled', String(!d.sid));", fuente)
        self.assertNotIn("$('#btn-prog').disabled", fuente)
        self.assertIn("if (!d || !d.sid) { toast('Guardá el informe para poder programar su envío.'); return; }", fuente)

    def test_mandar_de_verdad_pide_dos_toques_y_ver_como_queda_no_manda(self):
        fuente = self.fuente()
        self.assertIn("probarProg(false)", fuente)
        self.assertIn("{enviar, canales, formato}", fuente)                    # el formato del formulario viaja siempre: lo que se ve y lo que se manda es lo que se eligió
        bloque = fuente[fuente.index("$('#prog-enviar').addEventListener"):fuente.index("$('#prog').addEventListener('change'")]
        self.assertIn("if (!PG.armado)", bloque)
        self.assertIn("Tocá otra vez para mandarlo de verdad", fuente)
        self.assertEqual(bloque.count("probarProg(true)"), 1)                  # el único camino a un envío real es el segundo toque

    def test_lo_que_viene_del_servidor_pasa_por_esc_o_textcontent(self):
        fuente = self.fuente()
        for pieza in ("esc(c.nombre)", "esc(c.id)", "esc(r.slot_texto)", "esc(r.canal)", "esc(r.detalle)", "esc(h)", "esc(TEXTO_ESTADO_ENVIO[r.estado] || r.estado)"):
            self.assertTrue(pieza in fuente, pieza)
        for pieza in ("e.textContent = texto || ''", "$('#prog-prev').textContent = textoDeProba(j, canales)", "$('#prog-estado').textContent =", "$('#prog-t').textContent ="):
            self.assertTrue(pieza in fuente, pieza)
        self.assertNotIn("$('#prog-prev').innerHTML", fuente)
        self.assertNotIn("$('#prog-msj').innerHTML", fuente)

    def test_la_programacion_no_toca_la_deteccion_de_cambios_del_informe(self):
        """La programación vive aparte (por el informe guardado): no entra en `foto`, así que programar no marca el informe como «sin guardar»."""
        fuente = self.fuente()
        self.assertIn("const foto = d => JSON.stringify([d.nombre, d.def, d.grafico]);", fuente)
        self.assertNotIn("PG", fuente[fuente.index("const foto ="):fuente.index("function nuevoDoc")])


class TestFase4aRevisionEnLaPantalla(unittest.TestCase):
    """Lo que encontró la revisión independiente de la pantalla de «Traer de DelPro» (15 de 15 hallazgos de uso confirmados y 4 de la regresión)."""

    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def bloque(self, desde, hasta):
        f = self.fuente()
        return f[f.index(desde):f.index(hasta, f.index(desde))]

    @unittest.skipUnless(shutil.which("node"), "hace falta node para correr el JavaScript de la pantalla")
    def test_la_lista_de_es_uno_de_entiende_la_coma_decimal_y_el_cero(self):
        codigo = ("const CAMPOS = {Num: {tipo: 'num'}, Txt: {tipo: 'txt'}};\n" + self.bloque("/* «Es uno de» lleva una lista", "function limpiaDef(") +
                  "\nconsole.log(JSON.stringify({a: listaDe('38,5, 40', 'num'), b: listaDe('1; 2; 3', 'num'), c: listaDe('Rodeo 1, Rodeo 2', 'txt'), d: listaDe([0, 1], 'num'),"
                  " e: condLimpia({campo: 'Num', op: 'en', valor: '38,5; 40'}), f: condLimpia({campo: 'Num', op: '=', valor: 0}), g: condLimpia({campo: 'Num', op: '=', valor: ''}),"
                  " h: condLimpia({campo: 'Num', op: 'en', valor: [0, 1]}), i: condLimpia({campo: 'Txt', op: 'en', valor: 'a, b'})}));")
        r = subprocess.run(["node", "-e", codigo], capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(r.stdout)
        self.assertEqual(s["a"], ["38,5", "40"])                                  # «38,5» es UN número: con «=» el mismo texto vale 38,5
        self.assertEqual(s["b"], ["1", "2", "3"])
        self.assertEqual(s["c"], ["Rodeo 1", "Rodeo 2"])                          # un texto sigue separándose con comas
        self.assertEqual(s["d"], [0, 1])
        self.assertEqual(s["e"], {"campo": "Num", "op": "en", "valor": ["38,5", "40"]})
        self.assertEqual(s["f"], {"campo": "Num", "op": "=", "valor": 0})          # el número 0 es un valor, no «vacío»
        self.assertIsNone(s["g"])
        self.assertEqual(s["h"], {"campo": "Num", "op": "en", "valor": [0, 1]})
        self.assertEqual(s["i"], {"campo": "Txt", "op": "en", "valor": ["a", "b"]})

    @unittest.skipUnless(shutil.which("node"), "hace falta node para correr el JavaScript de la pantalla")
    def test_el_limite_de_filas_viaja_con_el_informe(self):
        """«Las 10 vacas con más producción»: la IA arma `limite: 10`, y Actualizar, Guardar, Exportar y el envío programado lo ignoraban (limpiaDef no lo copiaba)."""
        codigo = ("const CAMPOS = {};\n" + self.bloque("/* «Es uno de» lleva una lista", "/* Deja la definición coherente") +
                  "\nconst base = {columnas: [{campo: 'Number'}], condiciones: [], incluir_descartados: false};\n"
                  "console.log(JSON.stringify({a: limpiaDef(Object.assign({limite: 10}, base)), b: limpiaDef(Object.assign({limite: 0}, base)), c: limpiaDef(base),"
                  " d: limpiaDef(Object.assign({limite: ''}, base))}));")
        r = subprocess.run(["node", "-e", codigo], capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(r.stdout)
        self.assertEqual(s["a"]["limite"], 10)
        for k in ("b", "c", "d"):
            self.assertNotIn("limite", s[k], k)                                   # sin límite (0, vacío o ausente) no se manda nada

    def test_el_limite_se_ve_y_se_puede_cambiar(self):
        f = self.fuente()
        self.assertIn('data-chg="lim"', f)
        self.assertIn("a === 'lim'", f)
        self.assertEqual(sin_manejador(f), [])
        manejador = self.bloque("else if (a === 'lim')", "else if (a === 'cv')") if "else if (a === 'lim')" in f else f[f.index("a === 'lim'"):f.index("a === 'lim'") + 400]
        self.assertIn("delete d.def.limite", manejador)                              # vacío = sin límite
        self.assertIn("5000", f)                                                     # el mismo tope que el servidor

    def test_un_limite_elegido_no_se_presenta_como_una_falla(self):
        f = self.fuente()
        zona = f[f.index("const extra = r.truncado"):f.index("const extra = r.truncado") + 900]
        self.assertIn("d.def.limite", zona)
        self.assertIn("Se muestran las primeras", zona)

    def test_el_cuadro_de_programar_avisa_que_las_fechas_son_fijas(self):
        f = self.fuente()
        self.assertIn("PG.fechasFijas", f)
        self.assertIn("tipoDe(c.campo) === 'fecha'", f)
        self.assertIn("no se corren solas", f)

    def test_cambiar_el_operador_solo_redibuja_al_entrar_o_salir_de_es_uno_de_y_no_pierde_el_foco(self):
        b = self.bloque("else if (a === 'co') {", "else if (a === 'cv')")
        self.assertIn("(antes === 'en') !== (c.op === 'en')", b)
        self.assertIn("enfocar('#filters [data-chg=\"co\"]", b)                    # enfocar() = querySelector + focus()
        self.assertIn("l.length ? l[0] : ''", b)                                  # el primero de la lista, aunque sea el 0 (`l[0] || ''` lo perdía)
        self.assertNotIn("[0] || ''", b)

    def test_los_errores_de_traer_van_dentro_del_cuadro_y_no_en_un_toast(self):
        f = self.fuente()
        traer = self.bloque("async function traerDeDelpro", "function renderImportado")
        # Un toast queda debajo del fondo del cuadro modal y no se lee: el único que hay sale cuando el cuadro YA se cerró (la respuesta llegó tarde).
        self.assertEqual(traer.count("toast("), 1)
        self.assertIn("else toast('Traje «'", traer)
        self.assertLess(traer.index("const aqui = $('#delpro').open;"), traer.index("else toast("))
        self.assertIn("errorDelpro(", traer)
        self.assertRegex(f, r'<div class="msg err" id="dp-err" role="alert" hidden>')

    def test_ni_el_aviso_ni_el_resumen_del_cuadro_son_regiones_vivas(self):
        f = self.fuente()
        self.assertNotRegex(f, r'id="importado"[^>]*aria-live')
        self.assertNotRegex(f, r'id="dp-resumen"[^>]*aria-live')

    def test_al_traer_el_foco_queda_en_el_aviso(self):
        f = self.fuente()
        self.assertIn("function enfocarAviso()", f)
        self.assertIn("enfocarAviso();", self.bloque("async function traerDeDelpro", "function renderImportado"))
        self.assertIn('tabindex="-1" role="region" aria-label="Aviso: ', f)

    def test_el_motivo_de_cada_condicion_llega_a_la_pantalla(self):
        f = self.fuente()
        self.assertIn("items: j.diferencias.filter(x => x.gravedad === g).map(x => ({texto: x.texto, detalle: x.detalle || []}))", f)
        self.assertIn("'<ul class=\"det\">'", f)
        self.assertIn("typeof t === 'string'", f)                                 # lo que arma la IA sigue siendo texto plano

    def test_un_informe_completo_con_avisos_no_es_verde_a_secas(self):
        f = self.fuente()
        self.assertIn("casi: ['Casi igual', 'ok']", f)
        self.assertIn("const estadoDe = (estado, n) => estado === 'completo' && n ? 'casi' : estado;", f)
        self.assertIn("(est === 'completo' ? ' pri' : '')", f)                    # el botón destacado es solo para el que no tiene ningún aviso

    def test_los_plurales_y_los_nombres_de_los_botones(self):
        f = self.fuente()
        self.assertIn("const plural = (n, uno, varios)", f)
        self.assertIn("plural(i.columnas.total, 'columna', 'columnas')", f)
        self.assertIn("'1 completo'", f)
        self.assertIn('aria-label="Traer «\' + esc(i.nombre) + \'»"', f)          # 66 botones «Traer» iguales no se distinguen con un lector de pantalla
        self.assertIn("filtro sin traer: puede mostrar más animales", f)          # la consecuencia a la vista, no solo en un tooltip

    def test_el_nombre_en_castellano_de_lo_que_falta_se_muestra(self):
        self.assertIn("f.nombre ? esc(f.nombre) + ' <small>(' + esc(f.campo) + ')</small>'", self.fuente())

    def test_guardar_un_informe_con_condiciones_que_no_se_trajeron_lo_avisa(self):
        f = self.fuente()
        g = self.bloque("async function guardar(d, comoNuevo)", "async function exportar(d)")
        self.assertIn("avisoAlGuardar(d)", g)
        self.assertIn("function toast(m, ms)", f)
        self.assertIn("AL_GUARDAR_DELPRO = 'Ojo: no trae todo lo que tenía en DelPro, así que puede mostrar animales de más o de menos.'", f)
        self.assertIn("alGuardar: AL_GUARDAR_DELPRO", f)                                       # lo que se trae de DelPro avisa lo mismo que lo que se reabre

    @unittest.skipUnless(shutil.which("node"), "hace falta node para correr el JavaScript de la pantalla")
    def test_el_aviso_al_guardar_solo_sale_si_falta_algo_que_cambia_los_animales(self):
        linea = [l for l in self.fuente().splitlines() if l.startswith("function avisoAlGuardar(")][0]
        codigo = (linea + "\nconst con = {importado: {alGuardar: 'Ojo: falta algo', grupos: [{clase: 'columnas', items: []}, {clase: 'contenido', items: ['x']}]}};\n"
                  "const sin = {importado: {alGuardar: 'Ojo: falta algo', grupos: [{clase: 'columnas', items: ['y']}, {clase: 'info', items: []}]}};\n"
                  "console.log(JSON.stringify([avisoAlGuardar(con), avisoAlGuardar(sin), avisoAlGuardar({}), avisoAlGuardar({importado: {grupos: [{clase: 'contenido'}]}}), avisoAlGuardar({importado: {alGuardar: 'x'}})]));")
        r = subprocess.run(["node", "-e", codigo], capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["Ojo: falta algo", "", "", "", ""])        # solo con algo que cambia QUÉ animales aparecen y un texto para decirlo

    def test_cerrar_el_aviso_de_algo_que_cambia_los_animales_deja_una_linea(self):
        f = self.fuente()
        self.assertIn('data-act="abririmp"', f)
        self.assertIn("a === 'abririmp'", f)
        self.assertEqual(sin_manejador(f), [])

    def test_si_falla_la_red_el_mensaje_no_es_el_del_navegador(self):
        f = self.fuente()
        self.assertIn("No se pudo conectar con LactIA. Revisá la conexión y probá de nuevo.", f)
        self.assertNotIn("Failed to fetch", f)

    def test_el_estado_vacio_y_la_falla_no_se_ven_igual(self):
        f = self.fuente()
        self.assertIn("j && j.vacio ?", f)
        self.assertIn('<div class="msg warn" role="alert">', f)

    def test_la_barra_de_condiciones_entra_en_un_celular(self):
        f = self.fuente()
        self.assertRegex(f, r"\.cond\{[^}]*flex-wrap:wrap[^}]*max-width:100%")
        self.assertRegex(f, r"\.cond \.in\.v\.wide\{[^}]*min-width:0")

    def test_los_titulos_del_aviso_se_leen(self):
        f = self.fuente()
        self.assertRegex(f, r"\.imp h4\{[^}]*color:var\(--muted\)")                # --subtle sobre el fondo del aviso da 4,10:1 (WCAG AA pide 4,5:1)
        self.assertIn("'<h4 class=\"' + esc(x.clase) + '\">'", f)


class TestFase4bcRevisionEnLaPantalla(unittest.TestCase):
    """Los hallazgos de pantalla de «Describí el informe» y «Programar envío» (revisión independiente del 24/11/2026: 14 de 15 confirmados, todos arreglados)."""

    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def bloque(self, desde, hasta):
        f = self.fuente()
        return f[f.index(desde):f.index(hasta, f.index(desde))]

    def test_el_latido_del_hilo_se_ve_y_avisa_si_hace_rato_que_no_revisa(self):
        f = self.fuente()
        self.assertIn('id="prog-latido"', f)
        b = self.bloque("function renderLatido()", "async function cargarRegistroProg()")
        self.assertIn("ultima_pasada_hace_s", b)
        self.assertIn("no revisa desde", b)
        self.assertIn("hace > 300", b)                                           # más de 5 minutos: el hilo corre cada 1
        self.assertIn("renderLatido();", self.bloque("function renderProg()", "function renderEnvio()"))

    def test_guardar_no_cambia_la_pausa_y_pausar_usa_lo_que_ya_estaba_guardado(self):
        b = self.bloque("$('#prog-guardar').addEventListener", "$('#prog-ver')")
        self.assertIn("enPausa", b)
        self.assertIn("guardarProg(!enPausa", b)                                  # un envío en pausa sigue en pausa al guardar otro horario
        self.assertIn("{dias: e.dias, horas: e.horas, canales: e.canales, formato: e.formato, activo: !e.activo}", b)     # pausar no depende del formulario a medio editar

    def test_enviar_ahora_sin_canales_avisa_y_un_doble_clic_no_lo_confirma(self):
        b = self.bloque("$('#prog-enviar').addEventListener", "$('#prog').addEventListener('change'")
        self.assertIn("if (!PG.canales.length)", b)
        self.assertIn("Elegí por dónde mandarlo", b)
        self.assertIn("Date.now() - PG.armado < 700", b)
        self.assertEqual(b.count("probarProg(true)"), 1)

    def test_la_respuesta_de_otro_informe_no_se_pinta_en_este_cuadro(self):
        b = self.bloque("async function probarProg(enviar)", "async function cargarRegistroProg")
        self.assertIn("if (sid !== PG.sid) return;", b)
        self.assertIn("if (PG.ocupado && PG.ocupado.sid === sid) return;", b)

    def test_un_canal_apagado_que_estaba_tildado_se_puede_destildar(self):
        self.assertIn("(c.disponible || PG.canales.indexOf(c.id) >= 0 ? '' : ' disabled')", self.fuente())

    def test_el_cuadro_no_hereda_lo_del_informe_anterior(self):
        b = self.bloque("async function abrirProg()", "async function cargarProg()")
        self.assertIn("PG.dias = []; PG.horas = []; PG.canales = []; PG.formato = 'excel';", b)
        self.assertIn("PG.fallo = false;", b)
        self.assertIn("$('#prog-guardar').disabled = !j || quieto;", self.fuente())

    def test_una_respuesta_tardia_de_la_ia_no_cambia_la_pantalla_de_golpe(self):
        f = self.fuente()
        self.assertIn("function abrirComoNuevo(nombre, definicion, origen, enSegundoPlano)", f)
        self.assertIn("if (enSegundoPlano) { renderTabs(); return d; }", f)
        b = self.bloque("async function armarConFrase()", "/* ---------- programar el envío")
        self.assertIn("const aqui = activeId === 'list' && !document.querySelector('dialog[open]');", b)
        self.assertIn("if (aqui) enfocarAviso();", b)

    def test_un_resultado_que_llega_tarde_no_borra_el_aviso_de_que_cambiaste_el_informe(self):
        b = self.bloque("async function ejecutar(d)", "async function refrescarLista()")
        self.assertIn("enviado = JSON.stringify(cuerpo)", b)
        self.assertIn("d.stale = JSON.stringify(cuerpoEjecutar(d)) !== enviado;", b)
        self.assertNotIn("d.stale = false;", b)

    def test_un_doble_clic_no_abre_dos_pestanas_ni_guarda_dos_veces(self):
        f = self.fuente()
        self.assertIn("async function una(clave, accion)", f)
        for pieza in ("una('abrir' + b.dataset.id", "una('dptraer'", "una('guardar', () => guardar(docAct(), false))", "una('guardar', () => guardar(docAct(), true))"):
            self.assertIn(pieza, f, pieza)

    def test_si_falla_el_catalogo_no_dice_que_la_ia_no_esta_configurada(self):
        f = self.fuente()
        self.assertIn("IA.sinCatalogo", f)
        self.assertIn("No se pudo cargar la pantalla del todo. Recargá la página (F5)", f)
        b = self.bloque("(async function iniciar()", "</script>")
        self.assertIn("refrescarLista()", b)                                      # la lista y los envíos se esperan aunque falle el catálogo
        self.assertIn("refrescarEnvios()", b)
        self.assertIn("if (campos) {", b)

    def test_un_error_http_sin_cuerpo_no_se_muestra_como_error_502(self):
        f = self.fuente()
        self.assertIn("const errorHttp = estado => estado >= 500", f)
        self.assertEqual(f.count("errorHttp(r.status)"), 2)                      # api() y exportar()
        self.assertNotIn("('Error ' + r.status)", f)

    def test_los_canales_se_escriben_con_su_nombre_y_el_aviso_del_boton_no_dice_solo(self):
        f = self.fuente()
        self.assertIn("const nombreCanal = id =>", f)
        self.assertIn("bien.map(nombreCanal)", f)
        self.assertNotIn("Mandar este informe solo,", f)
        self.assertEqual(f.count("Mandar este informe automáticamente"), 2)

    def test_ningun_texto_de_la_pantalla_dice_ese_dia(self):
        """Regla del tambo: las fechas llevan el día de la semana, nunca «ayer» ni «ese día» (dos textos del gráfico de la Fase 3 la rompían)."""
        f = self.fuente()
        for malo in ("Rodeo de ese día", "El rodeo es el de ese día"):
            self.assertNotIn(malo, f, malo)
        self.assertIn("Rodeo de cada fecha", f)

    @unittest.skipUnless(shutil.which("node"), "hace falta node para correr el JavaScript de la pantalla")
    def test_los_ids_internos_que_cita_la_ia_se_reemplazan_por_el_nombre_del_campo(self):
        linea = [l for l in self.fuente().splitlines() if l.startswith("const sinIds = ")][0]
        codigo = ("const CAMPOS = {SevenDayAverage: {etiqueta: 'Promedio producción diaria de leche 7 d.'}, CurrentDIM: {etiqueta: 'Días en ordeño'}};\n" + linea +
                  "\nconsole.log(JSON.stringify([sinIds('Tomé SevenDayAverage > 0 como en ordeño'), sinIds('Usé CurrentDIM y Foobar'), sinIds('Nada técnico: rodeo 3')]));")
        r = subprocess.run(["node", "-e", codigo], capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        a, b, c = json.loads(r.stdout)
        self.assertEqual(a, "Tomé «Promedio producción diaria de leche 7 d.» > 0 como en ordeño")
        self.assertEqual(b, "Usé «Días en ordeño» y Foobar")                       # lo que no es del catálogo se deja como está
        self.assertEqual(c, "Nada técnico: rodeo 3")


RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node") or next((n for n in (r"C:\Program Files\nodejs\node.exe", "/usr/bin/node", "/usr/local/bin/node") if os.path.exists(n)), None)


@unittest.skipUnless(NODE, "hace falta node para correr el JavaScript de la pantalla")
class TestPantallaEjecutada(unittest.TestCase):
    """El JavaScript REAL de `templates/informes.html`, ejecutado con Node sobre un DOM mínimo y una API simulada (`tests/js/pantalla_informes.js`, `mini_dom.js`).

    Las pruebas de arriba buscan texto en el código de la plantilla; estas, en cambio, hacen lo que hace el usuario (tocar, tocar dos veces, cambiar de informe mientras un pedido
    está en camino, usar el teclado) y miran lo que queda en pantalla y lo que se le manda al servidor. Los casos viven en `tests/js/informes_pantalla.test.js`; Node los corre una
    sola vez para toda la clase. No hay red, ni base, ni canales: lo que llama a `fetch` lo atiende un manejador de mentira.
    """
    resultados = {}

    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, os.path.join("tests", "js", "informes_pantalla.test.js")], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", timeout=180)
        lineas = [l for l in (r.stdout or "").splitlines() if l.startswith("RESULTADOS ")]
        if not lineas:
            raise AssertionError("Node no llegó a dar resultados:\n" + (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
        cls.resultados = {x["nombre"]: x for x in json.loads(lineas[0][len("RESULTADOS "):])}

    def pasa(self, nombre):
        r = self.resultados.get(nombre)
        self.assertIsNotNone(r, "no existe el caso «%s» en tests/js/informes_pantalla.test.js" % nombre)
        self.assertTrue(r["ok"], "%s\n%s" % (nombre, r.get("error", "")))

    def test_todos_los_casos_de_la_pantalla_pasan(self):
        self.assertGreaterEqual(len(self.resultados), 20)
        fallan = ["%s\n%s" % (n, r.get("error", "")) for n, r in self.resultados.items() if not r["ok"]]
        self.assertEqual(fallan, [])

    def test_las_reglas_y_el_promedio_entre_fechas_en_la_pantalla(self):
        """Tanda 6: se agrupan y filtran como texto las reglas, se conservan sus datos al guardar y cada tipo se edita en su propio cuadro."""
        for nombre in ("unas reglas se tratan como un texto, se agrupan y viajan enteras al servidor", "un promedio conserva sus fechas y es un número en kg",
                       "editar unas reglas las valida como reglas y muestra los resultados posibles", "crear un promedio de leche entre fechas le pone nombre y agrega la columna",
                       "al editar un promedio, un nombre propio no se pisa al cambiar las fechas", "la cuenta de siempre sigue igual"):
            self.pasa("calculados: " + nombre)

    def test_es_uno_de_sobre_un_campo_calculado_separa_con_punto_y_coma(self):
        """[regresion #1] y [pantalla #1]: antes viajaba UNA cadena («3; 4») y el servidor la rechazaba, o «0,5; 1» se partía en 0, 5 y 1."""
        self.pasa("lista: un campo calculado separa con la regla de los números")
        self.pasa("lista: al salir de «es uno de» en un campo calculado queda el primer valor entero")

    def test_en_un_campo_entero_la_coma_tambien_separa(self):
        """[regresion #3]: «1,2,3» en Nº Lactación; con decimales la coma sigue siendo la decimal."""
        self.pasa("lista: en un campo entero la coma también separa; con decimales sigue siendo la coma decimal")
        self.pasa("lista: al salir de «es uno de» en un campo entero queda el primer valor")

    def test_una_respuesta_tardia_de_guardar_no_toca_el_cuadro_de_otro_informe(self):
        """[pantalla #2]."""
        self.pasa("programar: una respuesta tardía de «Guardar» no pinta ni escribe el cuadro de otro informe")
        self.pasa("programar: un error tardío del guardado de otro informe no se muestra")

    def test_el_registro_tardio_no_se_mezcla_y_el_cuadro_nuevo_arranca_vacio(self):
        """[pantalla #2]."""
        self.pasa("programar: el registro que llega tarde no se mezcla con el de otro informe")
        self.pasa("programar: al abrir el cuadro de otro informe se vacían el registro y el mensaje")

    def test_enviar_ahora_manda_el_formato_del_formulario_y_avisa_antes(self):
        """[pantalla #3]: antes mandaba lo guardado sin decirlo."""
        self.pasa("programar: «Enviar ahora» manda el formato del formulario y el primer toque dice qué va a salir")
        self.pasa("programar: «Ver cómo queda» también manda el formato y no dice que usa lo guardado")

    def test_se_muestra_el_texto_de_cada_canal(self):
        """[envios #6]: el correo, Telegram y WhatsApp no reciben el mismo texto."""
        self.pasa("programar: se muestra el texto de cada canal tildado, rotulado, y sin «textos» el del correo")

    def test_un_doble_clic_en_pausar_no_se_deshace(self):
        """[pantalla #4]: el segundo clic llegaba con el botón ya en «Reanudar» y lo deshacía."""
        self.pasa("programar: un doble clic en «Pausar envío» no se deshace solo, aunque el servidor conteste enseguida")
        self.pasa("programar: mientras se guarda, «Guardar» y «Pausar» quedan quietos y dicen «Guardando…»")

    def test_ver_como_queda_no_dice_mandando(self):
        """[pantalla #5]."""
        self.pasa("programar: la vista previa dice «Armando…» y no «Mandando…»; el envío sí dice «Mandando…»")

    def test_el_aviso_de_fechas_fijas_mira_lo_guardado(self):
        """[pantalla #7]."""
        self.pasa("programar: el aviso de fechas fijas mira lo guardado y no lo que está en pantalla sin guardar")

    def test_programar_un_informe_sin_guardar_explica_por_que_no(self):
        """[pantalla #10]."""
        self.pasa("programar: en un informe sin guardar el botón sigue activo y al tocarlo explica por qué no")

    def test_el_resumen_del_cuadro_de_delpro_cuenta_como_las_filas(self):
        """[pantalla #9]."""
        self.pasa("traer: el resumen cuenta con la misma regla que las filas (completo con avisos es «casi igual»)")

    def test_un_informe_traido_que_llega_tarde_no_cambia_la_pantalla(self):
        """[pantalla #6]."""
        self.pasa("traer: si el cuadro ya no está abierto cuando llega el informe, queda en una pestaña de fondo")
        self.pasa("traer: con el cuadro abierto el informe se abre y el foco queda en su aviso")

    def test_el_foco_del_teclado_no_se_pierde(self):
        """[pantalla #8]: al cambiar el campo de una condición, con «+ Condición» y al cerrar el aviso del informe traído."""
        self.pasa("foco: al cambiar el campo de una condición el foco sigue en el mismo selector")
        self.pasa("foco: «+ Condición» pasa el foco al campo de la fila nueva")
        self.pasa("foco: cerrar el aviso del informe traído deja el foco en «Ver el aviso» o, si no queda franja, en el nombre")


class TestBarraDelInforme(unittest.TestCase):
    """[regresion #4]: con «Programar» la barra de botones pasó de una línea a dos a 1280 px y «Actualizar», el botón principal, quedaba solo abajo."""

    def fuente(self):
        with open("templates/informes.html", encoding="utf-8") as f:
            return f.read()

    def test_sin_guardar_va_junto_al_nombre_y_no_entre_los_botones(self):
        f = self.fuente()
        i = f.index('<div class="acts">')
        barra = f[i:f.index("</div>", i)]
        self.assertNotIn('id="unsaved"', barra)
        self.assertIn('<label class="eyebrow" for="rname">Nombre del informe</label><span class="unsaved" id="unsaved" hidden>', f)
        self.assertRegex(f, r"\.lblrow\{[^}]*min-height:18px")                          # el alto fijo evita que el campo del nombre salte al aparecer el aviso

    def test_el_boton_de_programar_es_corto_y_dice_todo_en_su_nombre_accesible_y_su_ayuda(self):
        f = self.fuente()
        m = re.search(r'<button class="btn" id="btn-prog"([^>]*)>([^<]*)</button>', f)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "Programar")
        self.assertIn('aria-label="Programar envío"', m.group(1))                       # el nombre accesible contiene el rótulo visible
        self.assertIn('title="Mandar este informe automáticamente', m.group(1))


class TestMenu(unittest.TestCase):
    def test_los_permisos(self):
        admin = auth.PAGINAS_POR_ROL["admin"]
        self.assertIn("informes", admin)
        self.assertEqual(admin.index("informes"), admin.index("consultas") + 1)    # justo después de «Consultas e IA»
        self.assertNotIn("informes", auth.PAGINAS_POR_ROL["operario"])

    def test_la_entrada_del_menu_solo_para_el_administrador(self):
        with mock.patch.object(app, "_tambo_del_request", lambda: "ponderosa"):
            admin = cliente("admin").get("/").get_data(as_text=True)
            operario = cliente("operario").get("/").get_data(as_text=True)
        self.assertIn('data-pagina="informes"', admin)
        self.assertIn('id="page-informes"', admin)
        self.assertNotIn('data-pagina="informes"', operario)


if __name__ == "__main__":
    unittest.main()
