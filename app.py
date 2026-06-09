import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'troque-esta-chave-em-producao')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', app.config['SECRET_KEY'])
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=12)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///medestoque.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Railway usa postgres://, SQLAlchemy exige postgresql://
uri = app.config['SQLALCHEMY_DATABASE_URI']
if uri.startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = uri.replace('postgres://', 'postgresql://', 1)

db = SQLAlchemy(app)
jwt = JWTManager(app)

# CORS: libera todas as origens (seguro pois a API exige JWT em todas as rotas protegidas)
CORS(app, resources={r"/api/*": {"origins": "*"}},
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "DELETE", "OPTIONS"],
     supports_credentials=False)

# ─────────────────────────────────────────
# MODELOS
# ─────────────────────────────────────────

class Unidade(db.Model):
    __tablename__ = 'unidades'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    senha_hash = db.Column(db.String(256), nullable=False)
    perfil = db.Column(db.String(20), nullable=False)  # admin | gestor | funcionario | compras
    unidade_id = db.Column(db.Integer, db.ForeignKey('unidades.id'), nullable=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def to_dict(self):
        return {
            'id': self.id, 'nome': self.nome, 'email': self.email,
            'perfil': self.perfil, 'unidade_id': self.unidade_id
        }

class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    codigo = db.Column(db.String(50), nullable=False)
    tipo = db.Column(db.String(10), nullable=False)         # cx | un
    qtd_por_embalagem = db.Column(db.Integer, default=1)
    unidade_base = db.Column(db.String(20), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey('unidades.id'), nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint('codigo', 'unidade_id'),)

    def to_dict(self):
        return {
            'id': self.id, 'nome': self.nome, 'codigo': self.codigo,
            'tipo': self.tipo, 'qtd_por_embalagem': self.qtd_por_embalagem,
            'unidade_base': self.unidade_base, 'ativo': self.ativo
        }

class Contagem(db.Model):
    __tablename__ = 'contagens'
    id = db.Column(db.Integer, primary_key=True)
    mes = db.Column(db.Integer, nullable=False)
    ano = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='aberta')     # aberta | fechada
    unidade_id = db.Column(db.Integer, db.ForeignKey('unidades.id'), nullable=False)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    itens = db.relationship('ItemContagem', backref='contagem', lazy=True, cascade='all, delete-orphan')

class ItemContagem(db.Model):
    __tablename__ = 'itens_contagem'
    id = db.Column(db.Integer, primary_key=True)
    contagem_id = db.Column(db.Integer, db.ForeignKey('contagens.id'), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey('produtos.id'), nullable=False)
    leituras = db.Column(db.Integer, default=0)
    qtd_convertida = db.Column(db.Integer, default=0)
    lida_em = db.Column(db.DateTime, default=datetime.utcnow)
    produto = db.relationship('Produto')

class NotaFiscal(db.Model):
    __tablename__ = 'notas_fiscais'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), nullable=False)
    fornecedor = db.Column(db.String(150), nullable=False)
    valor_total = db.Column(db.Float, default=0)
    status = db.Column(db.String(20), default='pendente')   # pendente | confirmada
    unidade_id = db.Column(db.Integer, db.ForeignKey('unidades.id'), nullable=False)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)
    itens = db.relationship('ItemNF', backref='nf', lazy=True, cascade='all, delete-orphan')

class ItemNF(db.Model):
    __tablename__ = 'itens_nf'
    id = db.Column(db.Integer, primary_key=True)
    nf_id = db.Column(db.Integer, db.ForeignKey('notas_fiscais.id'), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey('produtos.id'), nullable=False)
    qtd = db.Column(db.Integer, nullable=False)
    produto = db.relationship('Produto')

# ─────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────

def current_user():
    return db.session.get(Usuario, int(get_jwt_identity()))

# ─────────────────────────────────────────
# ROTAS PÚBLICAS
# ─────────────────────────────────────────

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email', '').strip()
    senha = data.get('senha', '')
    user = Usuario.query.filter_by(email=email).first()
    if not user or not user.check_senha(senha):
        return jsonify({'ok': False, 'msg': 'E-mail ou senha incorretos.'}), 401
    token = create_access_token(identity=str(user.id))
    return jsonify({'ok': True, 'token': token, 'user': user.to_dict()})

# ─────────────────────────────────────────
# ME
# ─────────────────────────────────────────

@app.route('/api/me')
@jwt_required()
def me():
    return jsonify(current_user().to_dict())

# ─────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────

@app.route('/api/dashboard')
@jwt_required()
def dashboard():
    user = current_user()
    uid = user.unidade_id
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year).first()
    total_produtos = Produto.query.filter_by(unidade_id=uid, ativo=True).count()
    itens_lidos = 0
    divergencias = []
    if cont:
        itens_lidos = db.session.query(
            db.func.sum(ItemContagem.leituras)
        ).filter_by(contagem_id=cont.id).scalar() or 0
        nf_map = {}
        for nf in NotaFiscal.query.filter_by(unidade_id=uid, status='confirmada'):
            for i in nf.itens:
                nf_map[i.produto_id] = nf_map.get(i.produto_id, 0) + i.qtd
        for ic in cont.itens:
            diff = ic.qtd_convertida - nf_map.get(ic.produto_id, 0)
            if diff != 0:
                divergencias.append({
                    'produto': ic.produto.nome,
                    'nf': nf_map.get(ic.produto_id, 0),
                    'contado': ic.qtd_convertida,
                    'diff': diff
                })
    nfs_pendentes = NotaFiscal.query.filter_by(unidade_id=uid, status='pendente').count()
    return jsonify({
        'contagem': {'mes': cont.mes, 'ano': cont.ano, 'status': cont.status} if cont else None,
        'total_produtos': total_produtos,
        'itens_lidos': itens_lidos,
        'divergencias': divergencias,
        'nfs_pendentes': nfs_pendentes
    })

# ─────────────────────────────────────────
# CONTAGEM
# ─────────────────────────────────────────

@app.route('/api/contagem')
@jwt_required()
def get_contagem():
    user = current_user()
    uid = user.unidade_id
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year).first()
    if not cont:
        cont = Contagem(mes=hoje.month, ano=hoje.year, unidade_id=uid)
        db.session.add(cont)
        db.session.commit()
    produtos = Produto.query.filter_by(unidade_id=uid, ativo=True).order_by(Produto.nome).all()
    return jsonify({
        'contagem': {'id': cont.id, 'mes': cont.mes, 'ano': cont.ano, 'status': cont.status},
        'produtos': [p.to_dict() for p in produtos]
    })

@app.route('/api/contagem/ler', methods=['POST'])
@jwt_required()
def ler_codigo():
    user = current_user()
    data = request.get_json() or {}
    codigo = data.get('codigo', '').strip()
    uid = user.unidade_id
    produto = Produto.query.filter_by(codigo=codigo, unidade_id=uid, ativo=True).first()
    if not produto:
        return jsonify({'ok': False, 'msg': 'Código não encontrado — produto desconsiderado.'})
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year, status='aberta').first()
    if not cont:
        return jsonify({'ok': False, 'msg': 'Não há contagem aberta para este mês.'})
    item = ItemContagem.query.filter_by(contagem_id=cont.id, produto_id=produto.id).first()
    if not item:
        item = ItemContagem(contagem_id=cont.id, produto_id=produto.id)
        db.session.add(item)
    item.leituras += 1
    item.qtd_convertida += produto.qtd_por_embalagem
    item.lida_em = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'ok': True, 'nome': produto.nome,
        'leituras': item.leituras, 'qtd': item.qtd_convertida, 'un': produto.unidade_base
    })

@app.route('/api/contagem/salvar', methods=['POST'])
@jwt_required()
def salvar_contagem():
    user = current_user()
    data = request.get_json() or {}
    uid = user.unidade_id
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year, status='aberta').first()
    if not cont:
        return jsonify({'ok': False, 'msg': 'Não há contagem aberta.'})
    for item_data in data.get('itens', []):
        pid = item_data.get('produto_id')
        qtd = item_data.get('qtd', 0)
        if not pid or qtd == 0:
            continue
        if not db.session.get(Produto, pid):
            continue
        item = ItemContagem.query.filter_by(contagem_id=cont.id, produto_id=pid).first()
        if not item:
            item = ItemContagem(contagem_id=cont.id, produto_id=pid)
            db.session.add(item)
        item.leituras = 1
        item.qtd_convertida = qtd
        item.lida_em = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/contagem/resetar', methods=['POST'])
@jwt_required()
def resetar_contagem():
    user = current_user()
    if user.perfil not in ['gestor', 'admin']:
        return jsonify({'ok': False, 'msg': 'Acesso restrito.'}), 403
    uid = user.unidade_id
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year).first()
    if cont:
        db.session.delete(cont)
        db.session.commit()
    return jsonify({'ok': True, 'msg': 'Contagem resetada.'})

# ─────────────────────────────────────────
# PRODUTOS
# ─────────────────────────────────────────

@app.route('/api/produtos')
@jwt_required()
def get_produtos():
    user = current_user()
    lista = Produto.query.filter_by(unidade_id=user.unidade_id, ativo=True).order_by(Produto.nome).all()
    return jsonify([p.to_dict() for p in lista])

@app.route('/api/produtos', methods=['POST'])
@jwt_required()
def novo_produto():
    user = current_user()
    data = request.get_json() or {}
    nome = data.get('nome', '').strip()
    codigo = data.get('codigo', '').strip()
    tipo = data.get('tipo', 'un')
    qtd = int(data.get('qtd', 1))
    un = data.get('un', 'un')
    if not nome or not codigo:
        return jsonify({'ok': False, 'msg': 'Preencha nome e código.'}), 400
    if Produto.query.filter_by(codigo=codigo, unidade_id=user.unidade_id).first():
        return jsonify({'ok': False, 'msg': 'Código já cadastrado.'}), 400
    p = Produto(
        nome=nome, codigo=codigo, tipo=tipo,
        qtd_por_embalagem=qtd if tipo == 'cx' else 1,
        unidade_base=un, unidade_id=user.unidade_id
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({'ok': True, 'produto': p.to_dict()})

@app.route('/api/produtos/<int:pid>', methods=['DELETE'])
@jwt_required()
def remover_produto(pid):
    user = current_user()
    p = db.session.get(Produto, pid)
    if p and p.unidade_id == user.unidade_id:
        p.ativo = False
        db.session.commit()
    return jsonify({'ok': True})

# ─────────────────────────────────────────
# NOTAS FISCAIS
# ─────────────────────────────────────────

@app.route('/api/nfs')
@jwt_required()
def get_nfs():
    user = current_user()
    uid = user.unidade_id
    lista = NotaFiscal.query.filter_by(unidade_id=uid).order_by(NotaFiscal.criada_em.desc()).all()
    prods = Produto.query.filter_by(unidade_id=uid, ativo=True).order_by(Produto.nome).all()

    def nf_dict(nf):
        return {
            'id': nf.id, 'numero': nf.numero, 'fornecedor': nf.fornecedor,
            'valor_total': float(nf.valor_total or 0), 'status': nf.status,
            'criada_em': nf.criada_em.strftime('%d/%m/%Y'),
            'itens': [
                {'produto_id': i.produto_id, 'nome': i.produto.nome,
                 'un': i.produto.unidade_base, 'qtd': i.qtd}
                for i in nf.itens
            ]
        }

    return jsonify({'nfs': [nf_dict(n) for n in lista], 'produtos': [p.to_dict() for p in prods]})

@app.route('/api/nfs', methods=['POST'])
@jwt_required()
def nova_nf():
    user = current_user()
    data = request.get_json() or {}
    numero = data.get('numero', '').strip()
    fornecedor = data.get('fornecedor', '').strip()
    valor = float(data.get('valor', 0) or 0)
    nf = NotaFiscal(numero=numero, fornecedor=fornecedor, valor_total=valor, unidade_id=user.unidade_id)
    db.session.add(nf)
    db.session.flush()
    for item in data.get('itens', []):
        pid = item.get('produto_id')
        qtd = item.get('qtd')
        if pid and qtd:
            db.session.add(ItemNF(nf_id=nf.id, produto_id=int(pid), qtd=int(qtd)))
    db.session.commit()
    return jsonify({'ok': True, 'msg': f'NF {numero} registrada.'})

@app.route('/api/nfs/<int:nf_id>/confirmar', methods=['POST'])
@jwt_required()
def confirmar_nf(nf_id):
    user = current_user()
    nf = db.session.get(NotaFiscal, nf_id)
    if nf and nf.unidade_id == user.unidade_id:
        nf.status = 'confirmada'
        db.session.commit()
    return jsonify({'ok': True})

# ─────────────────────────────────────────
# RELATÓRIO
# ─────────────────────────────────────────

@app.route('/api/relatorio')
@jwt_required()
def get_relatorio():
    user = current_user()
    uid = user.unidade_id
    hoje = date.today()
    mes = int(request.args.get('mes', hoje.month))
    ano = int(request.args.get('ano', hoje.year))
    cont = Contagem.query.filter_by(unidade_id=uid, mes=mes, ano=ano).first()
    nf_map = {}
    for nf in NotaFiscal.query.filter_by(unidade_id=uid, status='confirmada'):
        for i in nf.itens:
            nf_map[i.produto_id] = nf_map.get(i.produto_id, 0) + i.qtd
    linhas = []
    if cont:
        for ic in cont.itens:
            nf_qtd = nf_map.get(ic.produto_id, 0)
            linhas.append({
                'produto': ic.produto.nome, 'un': ic.produto.unidade_base,
                'contado': ic.qtd_convertida, 'nf': nf_qtd,
                'diff': ic.qtd_convertida - nf_qtd
            })
    return jsonify({
        'linhas': linhas, 'mes': mes, 'ano': ano,
        'contagem': {
            'id': cont.id, 'status': cont.status, 'mes': cont.mes, 'ano': cont.ano
        } if cont else None
    })

@app.route('/api/relatorio/fechar', methods=['POST'])
@jwt_required()
def fechar_contagem():
    user = current_user()
    uid = user.unidade_id
    hoje = date.today()
    cont = Contagem.query.filter_by(unidade_id=uid, mes=hoje.month, ano=hoje.year, status='aberta').first()
    if cont:
        cont.status = 'fechada'
        db.session.commit()
    return jsonify({'ok': True, 'msg': 'Contagem fechada com sucesso.'})

# ─────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────

@app.route('/api/admin/usuarios')
@jwt_required()
def get_admin_usuarios():
    user = current_user()
    if user.perfil != 'admin':
        return jsonify({'ok': False, 'msg': 'Acesso restrito.'}), 403
    return jsonify({
        'usuarios': [u.to_dict() for u in Usuario.query.order_by(Usuario.nome).all()],
        'unidades': [{'id': u.id, 'nome': u.nome} for u in Unidade.query.order_by(Unidade.nome).all()]
    })

@app.route('/api/admin/usuarios', methods=['POST'])
@jwt_required()
def novo_usuario():
    user = current_user()
    if user.perfil != 'admin':
        return jsonify({'ok': False, 'msg': 'Acesso restrito.'}), 403
    data = request.get_json() or {}
    nome = data.get('nome', '').strip()
    email = data.get('email', '').strip()
    senha = data.get('senha', '')
    perfil = data.get('perfil', 'funcionario')
    unidade_id = data.get('unidade_id') or None
    if Usuario.query.filter_by(email=email).first():
        return jsonify({'ok': False, 'msg': 'E-mail já cadastrado.'}), 400
    u = Usuario(nome=nome, email=email, perfil=perfil, unidade_id=unidade_id)
    u.set_senha(senha)
    db.session.add(u)
    db.session.commit()
    return jsonify({'ok': True, 'usuario': u.to_dict()})

@app.route('/api/admin/usuarios/<int:uid>', methods=['DELETE'])
@jwt_required()
def deletar_usuario(uid):
    user = current_user()
    if user.perfil != 'admin':
        return jsonify({'ok': False, 'msg': 'Acesso restrito.'}), 403
    u = db.session.get(Usuario, uid)
    if u and u.id != user.id:
        db.session.delete(u)
        db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/admin/unidades', methods=['POST'])
@jwt_required()
def nova_unidade():
    user = current_user()
    if user.perfil != 'admin':
        return jsonify({'ok': False, 'msg': 'Acesso restrito.'}), 403
    data = request.get_json() or {}
    nome = data.get('nome', '').strip()
    if not nome:
        return jsonify({'ok': False, 'msg': 'Nome obrigatório.'}), 400
    db.session.add(Unidade(nome=nome))
    db.session.commit()
    return jsonify({'ok': True})

# ─────────────────────────────────────────
# INICIALIZAÇÃO DO BANCO
# ─────────────────────────────────────────

def init_db():
    with app.app_context():
        db.create_all()
        if not Usuario.query.filter_by(email='admin@pastore.com').first():
            unidade = Unidade(nome='UTI Adulto')
            db.session.add(unidade)
            db.session.flush()
            admin = Usuario(nome='Administrador', email='admin@pastore.com',
                            perfil='admin', unidade_id=unidade.id)
            admin.set_senha('admin123')
            db.session.add(admin)
            db.session.commit()
            print('Banco iniciado. Login: admin@pastore.com / admin123')

# Roda na inicialização (idempotente)
init_db()

if __name__ == '__main__':
    app.run(debug=True)
