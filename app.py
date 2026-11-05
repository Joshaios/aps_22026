"""
Sistema de coleta de lixo reciclável (VERSÃO COMENTADA)
-------------------------------------------------------
Este texto entre aspas triplas é uma "docstring": documentação do arquivo.
Ele não executa nada, serve só para explicar.

Telas:
  /login            -> login da empresa
  /cadastro         -> cadastro de nova empresa
  /empresa          -> painel da empresa (nova solicitação + status das solicitações)
  /coletor/login    -> login do coletor
  /coletor          -> painel do coletor (busca empresa, marca coletado / não coletado)

Como rodar:
  pip install flask
  python app_comentado.py
  Abra http://127.0.0.1:5000
"""

# ============================================================================
# 1. IMPORTS (ferramentas que vamos usar)
# ============================================================================
import os                      # ler variáveis de ambiente e montar caminhos de arquivos
import sqlite3                 # biblioteca do Python para conversar com o banco SQL (SQLite)
from functools import wraps    # ajuda a criar "decorators" (usado nos protetores de login)

from flask import (
    Flask,                     # cria a aplicação web
    flash,                     # guarda uma mensagem rápida para mostrar na próxima tela
    g,                         # espaço temporário que vive só durante UMA requisição
    redirect,                  # manda o navegador ir para outra página
    render_template_string,    # transforma um texto HTML (com {{ }} e {% %}) em página final
    request,                   # dados que o usuário enviou (formulários, parâmetros da URL)
    session,                   # "memória" do usuário entre páginas (é o que guarda o login)
    url_for,                   # gera o endereço de uma rota a partir do NOME da função
)

# Funções para criar e conferir senhas com hash (nunca guardamos a senha pura)
from werkzeug.security import check_password_hash, generate_password_hash


# ============================================================================
# 2. CONFIGURAÇÃO DA APLICAÇÃO
# ============================================================================
app = Flask(__name__)  # cria a aplicação Flask

# O Flask assina o cookie de sessão (o login) com esta chave.
# Tenta ler da variável de ambiente SECRET_KEY; se não existir, usa o texto padrão.
# EM PRODUÇÃO: defina uma chave própria e secreta.
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

# Caminho completo do arquivo do banco, na mesma pasta deste script.
#   __file__            -> caminho deste arquivo
#   os.path.abspath     -> transforma em caminho absoluto
#   os.path.dirname     -> pega só a pasta
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coleta.db")

# Os três níveis de periculosidade e suas descrições.
# Usado na janela flutuante, na tabela explicativa e na validação do formulário.
PERICULOSIDADES = {
    "Baixa": "Material comum, sem risco relevante ao manuseio (papel, papelão, plástico limpo).",
    "Média": "Exige cuidado e EPI básico: pode cortar, contaminar ou conter resíduos (vidro, metais, óleo de cozinha).",
    "Alta": "Risco à saúde ou ao meio ambiente: transporte e descarte especiais (pilhas, baterias, lâmpadas, eletrônicos, químicos).",
}

# Status considerados "em aberto" (a consulta do coletor usa estes mesmos valores no SQL)
STATUS_ABERTOS = ("Pendente", "Não coletado")


# ============================================================================
# 3. BANCO DE DADOS
# ============================================================================
def get_db():
    """Devolve a conexão com o banco, abrindo uma só vez por requisição."""
    if "db" not in g:                           # ainda não abrimos conexão nesta requisição?
        g.db = sqlite3.connect(DB_PATH)         # abre o arquivo do banco
        g.db.row_factory = sqlite3.Row          # permite acessar colunas pelo nome: linha["material"]
        g.db.execute("PRAGMA foreign_keys = ON")  # liga as chaves estrangeiras (vêm desligadas no SQLite)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    """Roda automaticamente no fim de cada requisição e fecha a conexão."""
    db = g.pop("db", None)      # tira a conexão de g (ou None se não existir)
    if db is not None:
        db.close()


# Comandos SQL que criam as tabelas. "IF NOT EXISTS" evita erro se já existirem.
SCHEMA = """
-- Tabela das empresas clientes
CREATE TABLE IF NOT EXISTS empresas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- número único gerado automaticamente
    nome       TEXT NOT NULL UNIQUE,                -- obrigatório e não pode repetir
    senha_hash TEXT NOT NULL                        -- senha guardada como hash
);

-- Tabela dos coletores (quem faz a coleta)
CREATE TABLE IF NOT EXISTS coletores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario    TEXT NOT NULL UNIQUE,
    senha_hash TEXT NOT NULL
);

-- Tabela das solicitações de coleta (é aqui que as solicitações ficam SALVAS)
CREATE TABLE IF NOT EXISTS solicitacoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id    INTEGER NOT NULL REFERENCES empresas(id),   -- liga a solicitação a uma empresa
    material      TEXT NOT NULL,
    kgs           REAL NOT NULL CHECK (kgs > 0),              -- o banco recusa kg zero ou negativo
    periculosidade TEXT NOT NULL CHECK (periculosidade IN ('Baixa','Média','Alta')),
    status        TEXT NOT NULL DEFAULT 'Pendente'            -- toda solicitação nova nasce Pendente
                  CHECK (status IN ('Pendente','Coletado','Não coletado')),
    observacao    TEXT,                                       -- anotação do coletor (opcional)
    criado_em     TEXT NOT NULL DEFAULT (datetime('now','localtime')),  -- data preenchida pelo banco
    atualizado_em TEXT                                        -- data da última mudança de status
);

-- Índice: deixa rápida a busca "solicitações desta empresa com tal status"
CREATE INDEX IF NOT EXISTS idx_solic_empresa ON solicitacoes(empresa_id, status);
"""


def init_db():
    """Cria as tabelas e o coletor padrão. Roda uma vez ao iniciar o programa."""
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)    # executa todos os comandos CREATE de uma vez
    # Insere o coletor padrão. "OR IGNORE" = se já existir, não faz nada (sem erro).
    # Os "?" são parâmetros: protegem contra SQL injection.
    db.execute("INSERT OR IGNORE INTO coletores (usuario, senha_hash) VALUES (?, ?)",
               ("coletor", generate_password_hash("coletor123")))
    db.commit()                 # grava de verdade no arquivo
    db.close()


# ============================================================================
# 4. PROTEÇÃO DAS PÁGINAS (exigir login)
# ============================================================================
def empresa_required(f):
    """Decorator: use @empresa_required em cima de uma rota para exigir empresa logada."""
    @wraps(f)                                   # mantém o nome/identidade da função original
    def wrapper(*a, **kw):
        if "empresa_id" not in session:         # não há empresa logada?
            return redirect(url_for("login"))   # manda para a tela de login
        return f(*a, **kw)                      # logada: executa a rota normalmente
    return wrapper


def coletor_required(f):
    """Igual ao anterior, mas para o coletor."""
    @wraps(f)
    def wrapper(*a, **kw):
        if "coletor_id" not in session:
            return redirect(url_for("coletor_login"))
        return f(*a, **kw)
    return wrapper


# ============================================================================
# 5. TEMPLATES (HTML das telas, guardados como texto dentro do Python)
# ============================================================================
# Sintaxe Jinja usada nos templates:
#   {{ variavel }}            -> escreve o valor da variável
#   {% if ... %} ... {% endif %}   -> condição
#   {% for x in lista %} ... {% else %} ... {% endfor %}  -> repetição ("else" = lista vazia)
#   {% extends base %} / {% block corpo %}  -> herda a estrutura da página BASE

# ---- BASE: estrutura comum a todas as páginas ----
BASE = """<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ titulo or 'Coleta reciclável' }}</title>
<style>
/* Cores guardadas como variáveis: mudar aqui muda no site inteiro */
:root{--papel:#f3f1ec;--tinta:#1d2b27;--musgo:#2f5d50;--musgo-esc:#214338;--linha:#d6d2c8;
--baixa:#3f8f5b;--media:#d49a1c;--alta:#c0392b;--branco:#fff}
*{box-sizing:border-box}  /* padding não aumenta o tamanho dos elementos */

/* Corpo da página, cabeçalho e área principal */
body{margin:0;font:16px/1.5 "Segoe UI",system-ui,sans-serif;background:var(--papel);color:var(--tinta)}
body{text-rendering:geometricPrecision;-webkit-font-smoothing:antialiased}
header{background:var(--musgo-esc);color:#fff;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
header strong{font-size:1.1rem;letter-spacing:.2px}
header a,header span{color:#dfeee8;font-size:.95rem;margin-left:16px}
main{max-width:980px;margin:28px auto;padding:0 20px}
h1{font-size:1.6rem;margin:0 0 4px} h2{font-size:1.2rem;margin:32px 0 10px}
.sub{color:#5b6762;margin:0 0 22px}
.box{background:var(--branco);border:1px solid var(--linha);border-radius:8px;padding:20px}

/* Campos de formulário */
label{display:block;font-weight:600;margin:14px 0 4px;font-size:.92rem}
input,select,textarea{width:100%;padding:10px 12px;border:1px solid #b9b5aa;border-radius:6px;font:inherit;background:#fff}
/* Contorno visível ao navegar por teclado (acessibilidade) */
input:focus,select:focus,button:focus-visible,a:focus-visible,textarea:focus{outline:3px solid #9cc7b8;outline-offset:1px}

/* Linha do formulário: 3 colunas no computador, 1 coluna no celular (menos de 700px) */
.row{display:grid;grid-template-columns:2fr 1fr 1.4fr;gap:14px}
@media(max-width:700px){.row{grid-template-columns:1fr}}

/* Botões: normal (.btn), secundário e vermelho (.btn-ruim, usado em "Não coletado") */
button,.btn{display:inline-block;background:var(--musgo);color:#fff;border:0;border-radius:6px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer;text-decoration:none}
button:hover,.btn:hover{background:var(--musgo-esc)}
.btn-sec{background:#fff;color:var(--musgo);border:1px solid var(--musgo)}
.btn-sec:hover{background:#eaf2ef}
.btn-ruim{background:var(--alta)} .btn-ruim:hover{background:#962d22}

/* Mensagens (flash): verde normal, vermelho quando tem a classe "erro" */
.msg{padding:10px 14px;border-radius:6px;margin-bottom:16px;background:#e5f1ec;border:1px solid #b6d6ca}
.msg.erro{background:#f9e3e0;border-color:#e3aaa3}

/* Tabelas */
.tabela{overflow-x:auto}  /* rolagem lateral em telas pequenas */
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--linha);border-radius:8px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--linha);vertical-align:top}
th{background:#ebe8e0;font-size:.9rem}

/* Etiquetas coloridas de periculosidade: verde / amarelo / vermelho */
.tag{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.85rem;font-weight:600;color:#fff}
.tag.Baixa{background:var(--baixa)} .tag.Média{background:var(--media);color:#2b2100} .tag.Alta{background:var(--alta)}
/* Cor do texto do status (a classe "Não" vem do primeiro pedaço de "Não coletado") */
.st{font-weight:600} .st.Coletado{color:var(--baixa)} .st.Pendente{color:#7a6a1a} .st.Não{color:var(--alta)}
.vazio{padding:22px;text-align:center;color:#5b6762}

/* Janela flutuante (<dialog>) e o fundo escurecido atrás dela */
dialog{border:0;border-radius:10px;padding:0;max-width:460px;width:92%;box-shadow:0 18px 50px rgba(0,0,0,.35)}
dialog::backdrop{background:rgba(20,30,27,.55)}
.dlg-h{padding:16px 20px;border-bottom:1px solid var(--linha)} .dlg-h h3{margin:0}
.dlg-b{padding:14px 20px 20px;display:grid;gap:10px}
/* Botões de escolha dentro da janela, com faixa colorida na esquerda */
.opt{display:block;width:100%;text-align:left;background:#fff;color:var(--tinta);border:2px solid var(--linha);padding:12px 14px}
.opt:hover{background:#f7f6f2} .opt small{display:block;font-weight:400;color:#4f5a56;margin-top:4px}
.opt.Baixa{border-left:8px solid var(--baixa)} .opt.Média{border-left:8px solid var(--media)} .opt.Alta{border-left:8px solid var(--alta)}
.pick{display:flex;gap:8px;align-items:center}
.pick input{cursor:pointer;background:#fff}
.auth{max-width:420px;margin:60px auto}  /* caixa centralizada das telas de login */
.inline{display:inline}
</style></head><body>

<!-- Cabeçalho: mostra quem está logado e o link "Sair" -->
<header>
  <strong>Coleta reciclável</strong>
  <nav>{% if session.get('empresa_nome') %}<span>{{ session['empresa_nome'] }}</span><a href="{{ url_for('logout') }}">Sair</a>
  {% elif session.get('coletor_nome') %}<span>Coletor: {{ session['coletor_nome'] }}</span><a href="{{ url_for('logout') }}">Sair</a>{% endif %}</nav>
</header>
<main>
<!-- Mostra as mensagens criadas com flash() (cat = categoria: "" ou "erro") -->
{% for cat, m in get_flashed_messages(with_categories=true) %}<div class="msg {{ cat }}" role="status">{{ m }}</div>{% endfor %}
<!-- Aqui entra o conteúdo específico de cada página -->
{% block corpo %}{% endblock %}
</main></body></html>"""

# ---- LOGIN: um só template usado por 3 telas (login empresa, cadastro, login coletor) ----
LOGIN = """{% extends base %}{% block corpo %}
<div class="auth box">
  <h1>{{ titulo_pagina }}</h1>          <!-- título muda conforme a tela -->
  <p class="sub">{{ subtitulo }}</p>
  <form method="post">                  <!-- POST: envia os dados para a mesma rota -->
    <label for="u">{{ rotulo_usuario }}</label>
    <input id="u" name="usuario" required autofocus>
    <label for="s">Senha</label>
    <input id="s" name="senha" type="password" required>
    {% if cadastro %}                   <!-- campo extra só na tela de cadastro -->
    <label for="s2">Confirmar senha</label>
    <input id="s2" name="senha2" type="password" required minlength="6">
    {% endif %}
    <p><button>{{ botao }}</button></p>
  </form>
  {{ links|safe }}                      <!-- |safe = permite HTML (os links vêm do nosso código) -->
</div>{% endblock %}"""

# ---- EMPRESA: formulário de solicitação + lista + tabela de periculosidade ----
EMPRESA = """{% extends base %}{% block corpo %}
<h1>Solicitar coleta</h1>
<p class="sub">Informe o material que sua empresa tem para recolhimento.</p>

<form class="box" method="post" action="{{ url_for('nova_solicitacao') }}">
  <div class="row">
    <!-- Nome da empresa: vem da sessão e não pode ser editado (readonly) -->
    <div><label>Nome da empresa</label>
      <input value="{{ session['empresa_nome'] }}" readonly aria-readonly="true"></div>
    <div><label for="kgs">Kgs</label>
      <input id="kgs" name="kgs" type="number" step="0.01" min="0.01" required></div>
    <div><label for="per">Periculosidade</label>
      <div class="pick">
        <!-- Campo somente leitura: ao clicar (ou Enter/Espaço) abre a janela flutuante -->
        <input id="per" name="periculosidade" placeholder="Clique para escolher" readonly required
               onclick="abrirJanela()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();abrirJanela()}">
      </div></div>
  </div>
  <label for="mat">Material</label>
  <input id="mat" name="material" placeholder="Ex.: papelão, garrafas PET, pilhas" required maxlength="120">
  <p><button>Enviar solicitação</button></p>
</form>

<!-- JANELA FLUTUANTE: elemento <dialog> nativo do HTML -->
<dialog id="janela" aria-labelledby="jt">
  <div class="dlg-h"><h3 id="jt">Escolha a periculosidade</h3></div>
  <div class="dlg-b">
    <!-- Gera um botão para cada nível do dicionário PERICULOSIDADES -->
    {% for nome, desc in perics.items() %}
    <button type="button" class="opt {{ nome }}" onclick="escolher('{{ nome }}')">{{ nome }}<small>{{ desc }}</small></button>
    {% endfor %}
    <button type="button" class="btn-sec" onclick="document.getElementById('janela').close()">Cancelar</button>
  </div>
</dialog>
<script>
// Abre a janela por cima de tudo (showModal escurece o fundo e prende o foco)
function abrirJanela(){document.getElementById('janela').showModal()}
// Escreve o nível escolhido no campo e fecha a janela
function escolher(v){document.getElementById('per').value=v;document.getElementById('janela').close()}
</script>

<h2>Minhas solicitações</h2>
<div class="tabela"><table>
  <tr><th>Nome da empresa</th><th>Material</th><th>Kgs</th><th>Periculosidade</th><th>Status</th><th>Solicitado em</th></tr>
  <!-- Uma linha por solicitação vinda do banco -->
  {% for s in solicitacoes %}
  <tr><td>{{ session['empresa_nome'] }}</td><td>{{ s.material }}</td><td>{{ '%.2f'|format(s.kgs) }}</td>
      <td><span class="tag {{ s.periculosidade }}">{{ s.periculosidade }}</span></td>
      <td><span class="st {{ s.status.split()[0] }}">{{ s.status }}</span>
          {% if s.observacao %}<br><small>{{ s.observacao }}</small>{% endif %}</td>
      <td>{{ s.criado_em }}</td></tr>
  <!-- Se a lista estiver vazia, o else do for mostra a mensagem abaixo -->
  {% else %}<tr><td colspan="6" class="vazio">Nenhuma solicitação ainda. Use o formulário acima para criar a primeira.</td></tr>
  {% endfor %}
</table></div>

<!-- Tabela explicativa da periculosidade, logo abaixo de tudo -->
<h2>O que significa cada nível de periculosidade</h2>
<div class="tabela"><table>
  <tr><th>Nível</th><th>Descrição</th><th>Exemplos</th><th>Cuidados</th></tr>
  <tr><td><span class="tag Baixa">Baixa</span></td><td>{{ perics['Baixa'] }}</td><td>Papel, papelão, plástico limpo</td><td>Manuseio comum</td></tr>
  <tr><td><span class="tag Média">Média</span></td><td>{{ perics['Média'] }}</td><td>Vidro, latas, óleo de cozinha</td><td>Luvas; embalar separado</td></tr>
  <tr><td><span class="tag Alta">Alta</span></td><td>{{ perics['Alta'] }}</td><td>Pilhas, baterias, lâmpadas, eletrônicos, químicos</td><td>Embalagem própria; equipe e veículo adequados</td></tr>
</table></div>
{% endblock %}"""

# ---- COLETOR: busca por empresa + solicitações em aberto + botões de status ----
COLETOR = """{% extends base %}{% block corpo %}
<h1>Painel do coletor</h1>
<p class="sub">Digite o nome da empresa para ver as solicitações em aberto.</p>

<!-- Busca por GET: o nome vai na URL (?empresa=...) -->
<form class="box" method="get">
  <label for="e">Nome da empresa</label>
  <div style="display:flex;gap:10px"><input id="e" name="empresa" value="{{ busca }}" list="lista" required autofocus>
  <button>Buscar</button></div>
  <!-- datalist = sugestões de autocompletar com os nomes das empresas -->
  <datalist id="lista">{% for n in nomes %}<option value="{{ n }}">{% endfor %}</datalist>
</form>

{% if busca %}                              <!-- só mostra resultados se o coletor buscou algo -->
  {% if not empresa %}
    <p class="msg erro">Nenhuma empresa encontrada com “{{ busca }}”.</p>
  {% else %}
  <h2>Solicitações em aberto — {{ empresa.nome }}</h2>
  {% for s in abertas %}                    <!-- um cartão por solicitação em aberto -->
  <div class="box" style="margin-bottom:12px">
    <p style="margin:0 0 8px"><strong>{{ s.material }}</strong> · {{ '%.2f'|format(s.kgs) }} kg ·
      <span class="tag {{ s.periculosidade }}">{{ s.periculosidade }}</span> ·
      <span class="st {{ s.status.split()[0] }}">{{ s.status }}</span>
      <br><small>Solicitado em {{ s.criado_em }}</small></p>
    <!-- Cada cartão é um formulário que envia o novo status para /coletor/status/<id> -->
    <form method="post" action="{{ url_for('atualizar_status', sid=s.id) }}">
      <!-- Campo oculto: para voltar à mesma busca depois de salvar -->
      <input type="hidden" name="empresa" value="{{ empresa.nome }}">
      <input name="observacao" placeholder="Observação (ex.: ninguém no local)" value="{{ s.observacao or '' }}" style="margin-bottom:10px">
      <!-- O botão clicado envia seu "value" no campo "status" -->
      <button name="status" value="Coletado">Marcar como coletado</button>
      <button name="status" value="Não coletado" class="btn-ruim">Não coletado</button>
    </form>
  </div>
  {% else %}<div class="box vazio">Nenhuma solicitação em aberto para esta empresa.</div>{% endfor %}
  {% endif %}
{% endif %}
{% endblock %}"""


def render(template, **ctx):
    """Atalho para desenhar uma página: passa 'base.html' para o {% extends base %} funcionar."""
    return render_template_string(template, base="base.html", **ctx)


# O {% extends %} precisa achar um "arquivo" chamado base.html.
# Como guardamos tudo em texto, o DictLoader funciona como uma pasta de templates na memória.
from jinja2 import DictLoader  # noqa: E402
app.jinja_loader = DictLoader({"base.html": BASE})


# ============================================================================
# 6. ROTAS DA EMPRESA
# ============================================================================
@app.route("/")
def index():
    """Página inicial: empresa logada vai ao painel, senão vai ao login."""
    return redirect(url_for("empresa" if "empresa_id" in session else "login"))


@app.route("/login", methods=["GET", "POST"])   # GET = mostrar a tela; POST = enviar o formulário
def login():
    if request.method == "POST":
        nome = request.form["usuario"].strip()  # lê o campo "usuario" e tira espaços das pontas
        # Procura a empresa pelo nome (o "?" é preenchido com segurança por (nome,))
        row = get_db().execute("SELECT * FROM empresas WHERE nome = ?", (nome,)).fetchone()
        # Existe a empresa E a senha digitada bate com o hash guardado?
        if row and check_password_hash(row["senha_hash"], request.form["senha"]):
            session.clear()                      # apaga qualquer sessão anterior
            # Guardar isto na sessão é o que significa "estar logado"
            session["empresa_id"], session["empresa_nome"] = row["id"], row["nome"]
            return redirect(url_for("empresa"))
        # Mesma mensagem para nome errado e senha errada (não revela quais empresas existem)
        flash("Nome da empresa ou senha incorretos.", "erro")
    return render(LOGIN, titulo_pagina="Entrar", subtitulo="Acesse com o nome e a senha da sua empresa.",
                  rotulo_usuario="Nome da empresa", botao="Entrar", cadastro=False,
                  links=f'<p><a href="{url_for("cadastro")}">Cadastrar empresa</a> · '
                        f'<a href="{url_for("coletor_login")}">Sou coletor</a></p>')


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        # Lê os três campos de uma vez
        nome, s1, s2 = request.form["usuario"].strip(), request.form["senha"], request.form["senha2"]
        # Validações, uma de cada vez
        if len(nome) < 2:
            flash("Informe o nome da empresa.", "erro")
        elif len(s1) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.", "erro")
        elif s1 != s2:
            flash("As senhas não conferem.", "erro")
        else:
            try:
                db = get_db()
                # Grava a empresa já com a senha transformada em hash
                db.execute("INSERT INTO empresas (nome, senha_hash) VALUES (?, ?)",
                           (nome, generate_password_hash(s1)))
                db.commit()
                flash("Empresa cadastrada. Faça o login.", "")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                # A coluna "nome" é UNIQUE: nome repetido cai aqui
                flash("Já existe uma empresa com esse nome.", "erro")
    return render(LOGIN, titulo_pagina="Cadastrar empresa", subtitulo="Crie o acesso da sua empresa.",
                  rotulo_usuario="Nome da empresa", botao="Cadastrar", cadastro=True,
                  links=f'<p><a href="{url_for("login")}">Voltar ao login</a></p>')


@app.route("/empresa")
@empresa_required                                # só entra quem está logado como empresa
def empresa():
    # Busca SOMENTE as solicitações da empresa logada (uma empresa nunca vê as de outra),
    # da mais nova para a mais antiga
    rows = get_db().execute(
        "SELECT * FROM solicitacoes WHERE empresa_id = ? ORDER BY id DESC",
        (session["empresa_id"],)).fetchall()
    return render(EMPRESA, titulo="Minha empresa", solicitacoes=rows, perics=PERICULOSIDADES)


@app.route("/empresa/solicitar", methods=["POST"])
@empresa_required
def nova_solicitacao():
    material = request.form["material"].strip()
    per = request.form.get("periculosidade", "")
    try:
        # Troca vírgula por ponto: aceita "12,5" além de "12.5"
        kgs = float(request.form["kgs"].replace(",", "."))
    except ValueError:
        kgs = 0                                  # não era número: a validação abaixo recusa
    # Valida no servidor (o navegador pode ser burlado)
    if not material or kgs <= 0 or per not in PERICULOSIDADES:
        flash("Preencha material, kgs (maior que zero) e periculosidade.", "erro")
    else:
        db = get_db()
        # SALVA a solicitação. O status "Pendente" e a data entram sozinhos (DEFAULT do banco)
        db.execute("INSERT INTO solicitacoes (empresa_id, material, kgs, periculosidade) VALUES (?,?,?,?)",
                   (session["empresa_id"], material, kgs, per))
        db.commit()
        flash("Solicitação enviada e salva.", "")
    return redirect(url_for("empresa"))          # volta ao painel (evita reenvio ao atualizar a página)


# ============================================================================
# 7. ROTAS DO COLETOR
# ============================================================================
@app.route("/coletor/login", methods=["GET", "POST"])
def coletor_login():
    if request.method == "POST":
        # Mesma lógica do login da empresa, mas consultando a tabela "coletores"
        row = get_db().execute("SELECT * FROM coletores WHERE usuario = ?",
                               (request.form["usuario"].strip(),)).fetchone()
        if row and check_password_hash(row["senha_hash"], request.form["senha"]):
            session.clear()
            session["coletor_id"], session["coletor_nome"] = row["id"], row["usuario"]
            return redirect(url_for("coletor"))
        flash("Usuário ou senha incorretos.", "erro")
    return render(LOGIN, titulo_pagina="Entrada do coletor", subtitulo="Acesso para quem faz a coleta.",
                  rotulo_usuario="Usuário", botao="Entrar", cadastro=False,
                  links=f'<p><a href="{url_for("login")}">Sou empresa</a></p>')


@app.route("/coletor")
@coletor_required
def coletor():
    db = get_db()
    busca = request.args.get("empresa", "").strip()   # nome digitado (vem da URL: ?empresa=...)
    empresa_row, abertas = None, []                   # valores padrão: nada encontrado
    if busca:
        # COLLATE NOCASE ignora maiúsculas/minúsculas: "padaria sol" acha "Padaria Sol"
        empresa_row = db.execute("SELECT * FROM empresas WHERE nome = ? COLLATE NOCASE", (busca,)).fetchone()
        if empresa_row:
            # Só as solicitações EM ABERTO: pendentes ou que já tentaram coletar sem sucesso
            abertas = db.execute(
                "SELECT * FROM solicitacoes WHERE empresa_id = ? AND status IN ('Pendente','Não coletado') "
                "ORDER BY id", (empresa_row["id"],)).fetchall()
    # Lista de nomes para o autocompletar da busca
    nomes = [r["nome"] for r in db.execute("SELECT nome FROM empresas ORDER BY nome")]
    return render(COLETOR, titulo="Painel do coletor", busca=busca, empresa=empresa_row,
                  abertas=abertas, nomes=nomes)


@app.route("/coletor/status/<int:sid>", methods=["POST"])   # <int:sid> = número da solicitação na URL
@coletor_required
def atualizar_status(sid):
    status = request.form.get("status")           # valor do botão clicado
    if status not in ("Coletado", "Não coletado"):  # aceita só os valores permitidos
        flash("Status inválido.", "erro")
    else:
        db = get_db()
        # Atualiza status, observação e data da mudança. A empresa verá isso ao recarregar a tela.
        # "or None": se a observação estiver vazia, grava NULL em vez de texto vazio
        db.execute("UPDATE solicitacoes SET status = ?, observacao = ?, atualizado_em = datetime('now','localtime') "
                   "WHERE id = ?", (status, request.form.get("observacao", "").strip() or None, sid))
        db.commit()
        flash(f"Solicitação marcada como “{status}”.", "")
    # Volta para a busca da mesma empresa (usa o campo oculto "empresa")
    return redirect(url_for("coletor", empresa=request.form.get("empresa", "")))


# ============================================================================
# 8. SAIR E INICIALIZAÇÃO
# ============================================================================
@app.route("/sair")
def logout():
    # Descobre se quem sai é coletor ou empresa para levar à tela de login certa
    destino = "coletor_login" if "coletor_id" in session else "login"
    session.clear()                               # apagar a sessão = deslogar
    return redirect(url_for(destino))


init_db()   # cria o banco e as tabelas assim que o arquivo é carregado

# Só inicia o servidor quando rodamos "python app_comentado.py" diretamente
if __name__ == "__main__":
    app.run(debug=True)   # debug=True recarrega ao editar o código. DESLIGUE em produção.
