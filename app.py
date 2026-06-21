from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date, timedelta
import json
import os

app = Flask(__name__, instance_path=os.path.dirname(os.path.abspath(__file__)))

# --- CONFIGURACIÓN PORTÁTIL (MULTI-ENTORNO) ---
LIGHTWEIGHT_MODE = os.environ.get('LIGHTWEIGHT_MODE', 'True').lower() == 'true'
DATABASE_URI = 'sqlite:///instance/restaurante_con_nombres.db'

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if LIGHTWEIGHT_MODE:
    print(">>> SISTEMA INICIADO EN MODO LIGERO (Optimizado para ARM/Fire TV Stick) <<<")
else:
    print(">>> SISTEMA INICIADO EN MODO DE ALTO RENDIMIENTO <<<")
    app.config['SQLALCHEMY_ECHO'] = True

db = SQLAlchemy(app)

# --- MODELOS ---
class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100))
    precio_base = db.Column(db.Float)
    negocio = db.Column(db.String(20))
    categoria = db.Column(db.String(50), default="General")
    opciones = db.relationship('Opcion', backref='producto', cascade="all, delete-orphan")

class Opcion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50))
    precio_extra = db.Column(db.Float, default=0.0)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'))

class Orden(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(50), nullable=False, default="Ticket_Generado") # Aumentamos default
    negocio = db.Column(db.String(20))
    tipo_servicio = db.Column(db.String(20))
    items_json = db.Column(db.Text)
    total = db.Column(db.Float)
    estado = db.Column(db.String(30), default='Abierta') # Abierta, Enviada_Cocina, Preparando, Lista_Para_Servir, Entregado, Cancelada, Reemplazada
    estado_pago = db.Column(db.String(20), default='Pendiente') # Pendiente, Pagada
    metodo_pago = db.Column(db.String(20))
    referencia = db.Column(db.String(100), nullable=True) # Campo para número de voucher / transferencia
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    creado_at = db.Column(db.DateTime, default=datetime.utcnow)
    preparacion_at = db.Column(db.DateTime, nullable=True)
    listo_at = db.Column(db.DateTime, nullable=True)

# --- CARGA LIGERA Y PORTÁTIL DE MENÚ (Sin dependencia de pandas) ---
def cargar_menu(csv_filepath='menu_data.csv'):
    # 1. Intentar cargar desde CSV usando la librería estándar csv (sin dependencias de terceros)
    if os.path.exists(csv_filepath):
        try:
            import csv
            with open(csv_filepath, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    nombre = row.get('nombre')
                    if nombre:
                        nombre = nombre.strip()
                    else:
                        continue
                    
                    try:
                        precio = float(row.get('precio_base', 0.0))
                    except (ValueError, TypeError):
                        precio = 0.0
                        
                    negocio = row.get('negocio', 'don_lomito').strip().lower()
                    categoria = row.get('categoria', 'General').strip().capitalize()
                    opciones_str = row.get('opciones', '').strip()
                    
                    nuevo_producto = Producto(nombre=nombre, precio_base=precio, negocio=negocio, categoria=categoria)
                    db.session.add(nuevo_producto)
                    db.session.commit()
                    
                    if opciones_str:
                        for opt_part in opciones_str.split(','):
                            opt_part = opt_part.strip()
                            if opt_part:
                                if ':' in opt_part:
                                    opt_name, opt_price = opt_part.split(':', 1)
                                    try:
                                        opt_price = float(opt_price.strip())
                                    except ValueError:
                                        opt_price = 0.0
                                else:
                                    opt_name = opt_part
                                    opt_price = 0.0
                                nueva_opcion = Opcion(nombre=opt_name.strip(), precio_extra=opt_price, producto_id=nuevo_producto.id)
                                db.session.add(nueva_opcion)
                        db.session.commit()
                    count += 1
                print(f"Menú cargado exitosamente desde CSV {csv_filepath} ({count} productos).")
                return
        except Exception as e:
            print(f"ERROR al cargar menú desde CSV: {e}")
            db.session.rollback()
            
    print("ADVERTENCIA: No se encontró 'menu_data.csv'. No se cargó menú inicial.")

# Se crea la base de datos y se carga el menú si está vacía
with app.app_context():
    db.create_all()
    # Migración automática: añadir columna referencia si no existe
    try:
        db.session.execute(db.text("ALTER TABLE orden ADD COLUMN referencia VARCHAR(100)"))
        db.session.commit()
        print("Migración: Columna 'referencia' agregada a la tabla 'orden'.")
    except Exception:
        db.session.rollback()
        
    # Migración automática: añadir columnas de auditoría de tiempos si no existen
    for col_name in ['creado_at', 'preparacion_at', 'listo_at']:
        try:
            db.session.execute(db.text(f"ALTER TABLE orden ADD COLUMN {col_name} DATETIME"))
            db.session.commit()
            print(f"Migración: Columna '{col_name}' agregada a la tabla 'orden'.")
        except Exception:
            db.session.rollback()
            
    # Poblar creado_at histórico con fecha_creacion si creado_at es null
    try:
        db.session.execute(db.text("UPDATE orden SET creado_at = fecha_creacion WHERE creado_at IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Migración automática: añadir columna estado_pago si no existe
    try:
        db.session.execute(db.text("ALTER TABLE orden ADD COLUMN estado_pago VARCHAR(20) DEFAULT 'Pendiente'"))
        db.session.commit()
        print("Migración: Columna 'estado_pago' agregada a la tabla 'orden'.")
    except Exception:
        db.session.rollback()

    # Actualizar estado_pago histórico: si estado es 'Pagada', set estado_pago = 'Pagada' y estado = 'Entregado'
    try:
        db.session.execute(db.text("UPDATE orden SET estado_pago = 'Pagada', estado = 'Entregado' WHERE estado = 'Pagada'"))
        db.session.commit()
        # Asegurar que cualquier registro nulo de estado_pago sea 'Pendiente'
        db.session.execute(db.text("UPDATE orden SET estado_pago = 'Pendiente' WHERE estado_pago IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Verificación de productos y carga de menú
    csv_exists = os.path.exists('menu_data.csv')
    productos_count = Producto.query.count()

    if productos_count == 0:
        if csv_exists:
            print("Base de datos de productos vacía, cargando menú...")
            cargar_menu('menu_data.csv')
        else:
            print("Base de datos de productos vacía y no se encontró menu_data.csv.")
    
    print("Base de datos cargada correctamente")


# --- RUTAS PRINCIPALES (POS) ---
@app.route('/')
def index():
    productos = Producto.query.all()
    cats_dl = db.session.query(Producto.categoria).filter(Producto.negocio=='don_lomito').distinct().all()
    cats_mi = db.session.query(Producto.categoria).filter(Producto.negocio=='mistun').distinct().all()
    ordenes = Orden.query.filter(
        Orden.estado_pago != 'Pagada',
        Orden.estado.notin_(['Cancelada', 'Reemplazada'])
    ).order_by(Orden.ticket_id.desc(), Orden.negocio.desc()).all()
    return render_template('index.html', productos=productos, ordenes=ordenes, 
                           cats_dl=[c[0] for c in cats_dl], cats_mi=[c[0] for c in cats_mi])

@app.route('/agregar_producto', methods=['POST'])
def agregar_producto():
    nuevo = Producto(
        nombre=request.form['nombre'], 
        precio_base=float(request.form['precio']), 
        negocio=request.form['negocio'],
        categoria=request.form.get('categoria', 'General').capitalize()
    )
    db.session.add(nuevo)
    db.session.commit()
    opciones = request.form.get('opciones', '')
    if opciones:
        for opt_part in opciones.split(','):
            opt_part = opt_part.strip()
            if opt_part:
                if ':' in opt_part:
                    opt_name, opt_price = opt_part.split(':', 1)
                    try:
                        opt_price = float(opt_price.strip())
                    except ValueError:
                        opt_price = 0.0
                else:
                    opt_name = opt_part
                    opt_price = 0.0
                nueva_opcion = Opcion(nombre=opt_name.strip(), precio_extra=opt_price, producto_id=nuevo.id)
                db.session.add(nueva_opcion)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/eliminar_producto/<int:id>', methods=['GET', 'POST'])
@app.route('/borrar_producto/<int:id>', methods=['GET', 'POST'])
def eliminar_producto(id):
    producto = Producto.query.get(id)
    if producto:
        db.session.delete(producto)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/eliminar_categoria', methods=['POST'])
def eliminar_categoria():
    negocio = request.form.get('negocio')
    categoria = request.form.get('categoria')
    productos = Producto.query.filter_by(negocio=negocio, categoria=categoria).all()
    for p in productos:
        db.session.delete(p)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/crear_orden', methods=['POST'])
def crear_orden():
    data = request.json
    orden_id = data.get('id')
    ticket_id = data.get('ticket_id')
    
    if orden_id: # Es una actualización de una sub-orden específica
        orden = Orden.query.get(orden_id)
        if orden:
            orden.items_json = json.dumps(data['items'])
            orden.total = float(data['total'])
            orden.tipo_servicio = data['tipo_servicio']
            orden.ticket_id = ticket_id
            # Conservamos el estado si ya está en proceso de cocina
            if orden.estado == 'Abierta': 
                orden.estado = 'Enviada_Cocina'
            db.session.commit()
            return jsonify({"status": "updated", "id": orden.id})
    else: # Es una nueva orden (o una parte de un pedido dividido)
        nueva = Orden(
            ticket_id=ticket_id,
            negocio=data['negocio'],
            tipo_servicio=data['tipo_servicio'],
            items_json=json.dumps(data['items']),
            total=float(data['total']),
            estado='Enviada_Cocina',
            creado_at=datetime.utcnow()
        )
        db.session.add(nueva)
        db.session.commit()
        return jsonify({"status": "created", "id": nueva.id})
    
    return jsonify({"status": "error", "message": "Operación de orden fallida"}), 400

@app.route('/reemplazar_grupo_ordenes/<string:ticket_id>', methods=['POST'])
def reemplazar_grupo_ordenes(ticket_id):
    Orden.query.filter_by(ticket_id=ticket_id).update({"estado": "Reemplazada"})
    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/cierre_caja')
def cierre_caja():
    periodo = request.args.get('periodo', 'dia')
    fecha_inicio = datetime.now()

    if periodo == 'dia':
        fecha_inicio = datetime.combine(date.today(), datetime.min.time())
    elif periodo == 'semana':
        fecha_inicio = datetime.now() - timedelta(days=datetime.now().weekday())
        fecha_inicio = fecha_inicio.replace(hour=0, minute=0, second=0)
    elif periodo == 'mes':
        fecha_inicio = datetime.now().replace(day=1, hour=0, minute=0, second=0)
    elif periodo == 'ano':
        fecha_inicio = datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0)

    ordenes_query = Orden.query.filter(
        Orden.estado_pago == 'Pagada', 
        Orden.fecha_creacion >= fecha_inicio
    ).order_by(Orden.fecha_creacion.desc()).all()
    
    # Procesar items para que sean legibles en el template
    for o in ordenes_query:
        o.items_lista = json.loads(o.items_json)

    total_general = sum(o.total for o in ordenes_query)
    total_dl = 0.0
    total_mi = 0.0
    for o in ordenes_query:
        if o.negocio == 'unificado':
            items = json.loads(o.items_json)
            for item in items:
                neg = item.get('negocio')
                qty = int(item.get('cantidad', 1))
                item_total = float(item.get('totalItem', 0.0)) * qty
                if neg == 'don_lomito':
                    total_dl += item_total
                elif neg == 'mistun':
                    total_mi += item_total
        elif o.negocio == 'don_lomito':
            total_dl += o.total
        elif o.negocio == 'mistun':
            total_mi += o.total
    
    por_metodo = {}
    for o in ordenes_query:
        por_metodo[o.metodo_pago] = por_metodo.get(o.metodo_pago, 0) + o.total

    return render_template('cierre.html', 
                           ordenes=ordenes_query, 
                           total_general=total_general,
                           total_dl=total_dl,
                           total_mi=total_mi,
                           por_metodo=por_metodo,
                           periodo=periodo,
                           hoy=date.today())


@app.route('/finalizar_pago', methods=['POST'])
def finalizar_pago():
    data = request.json
    ticket_id = data.get('ticket_id')
    orden_id = data.get('id')
    metodo = data.get('metodo')
    referencia = data.get('referencia')
    
    if ticket_id:
        # Cobrar todas las órdenes activas asociadas a la mesa/ticket
        ordenes = Orden.query.filter(
            Orden.ticket_id == ticket_id,
            Orden.estado_pago != 'Pagada',
            Orden.estado.notin_(['Cancelada', 'Reemplazada'])
        ).all()
        if len(ordenes) == 1:
            orden = ordenes[0]
            orden.estado_pago = 'Pagada'
            orden.metodo_pago = metodo
            orden.referencia = referencia
            db.session.commit()
            return jsonify({"status": "success", "scope": "individual", "count": 1})
        elif len(ordenes) > 1:
            items_unificados = []
            total_unificado = 0.0
            tipo_servicio = ordenes[0].tipo_servicio
            for o in ordenes:
                items_o = json.loads(o.items_json)
                items_unificados.extend(items_o)
                total_unificado += o.total
            
            # Encontrar el estado de producción más avanzado para el ticket unificado
            estados_prioridad = {'Entregado': 4, 'Lista_Para_Servir': 3, 'Preparando': 2, 'Enviada_Cocina': 1, 'Abierta': 0}
            best_state = 'Enviada_Cocina'
            best_priority = -1
            for o in ordenes:
                prio = estados_prioridad.get(o.estado, 0)
                if prio > best_priority:
                    best_priority = prio
                    best_state = o.estado

            nueva_unificada = Orden(
                ticket_id=ticket_id,
                negocio='unificado',
                tipo_servicio=tipo_servicio,
                items_json=json.dumps(items_unificados),
                total=total_unificado,
                estado=best_state,
                estado_pago='Pagada',
                metodo_pago=metodo,
                referencia=referencia,
                fecha_creacion=ordenes[0].fecha_creacion,
                creado_at=ordenes[0].creado_at,
                preparacion_at=ordenes[0].preparacion_at,
                listo_at=ordenes[0].listo_at
            )
            for o in ordenes:
                db.session.delete(o)
            db.session.add(nueva_unificada)
            db.session.commit()
            return jsonify({"status": "success", "scope": "ticket", "count": len(ordenes)})
        return jsonify({"status": "error", "message": "No hay órdenes activas para esta mesa"}), 400
    elif orden_id:
        # Cobrar orden de marca individual
        orden = Orden.query.get(orden_id)
        if orden:
            orden.estado_pago = 'Pagada'
            orden.metodo_pago = metodo
            orden.referencia = referencia
            db.session.commit()
            return jsonify({"status": "success", "scope": "individual"})
            
    return jsonify({"status": "error", "message": "Parámetros de cobro insuficientes"}), 400

@app.route('/api/active_orders_status')
def active_orders_status():
    ordenes = Orden.query.filter(
        Orden.estado_pago != 'Pagada',
        Orden.estado.notin_(['Cancelada', 'Reemplazada'])
    ).all()
    state = {o.id: o.estado for o in ordenes}
    return jsonify(state)

# --- RUTAS KDS (KITCHEN DISPLAY SYSTEM) ---
@app.route('/kds/<negocio>')
def kds_screen(negocio):
    return render_template('kds.html', negocio=negocio)

@app.route('/api/kds/orders/<negocio>')
def api_kds_orders(negocio):
    orders = Orden.query.filter(
        Orden.negocio == negocio,
        Orden.estado.notin_(['Entregado', 'Cancelada', 'Reemplazada'])
    ).order_by(Orden.fecha_creacion).all()
    
    output = []
    for o in orders:
        items = json.loads(o.items_json)
        for item in items:
            prod = Producto.query.filter_by(nombre=item['nombre'], negocio=item['negocio']).first() 
            if prod:
                item['categoria'] = prod.categoria # Asignamos la categoría
            else:
                item['categoria'] = 'Desconocida'

        output.append({
            "id": o.id,
            "ticket_id": o.ticket_id,
            "tipo_servicio": o.tipo_servicio,
            "total": o.total,
            "estado": o.estado,
            "fecha_creacion": o.fecha_creacion.isoformat(),
            "creado_at": o.creado_at.isoformat() if o.creado_at else None,
            "preparacion_at": o.preparacion_at.isoformat() if o.preparacion_at else None,
            "listo_at": o.listo_at.isoformat() if o.listo_at else None,
            "items": items
        })
    return jsonify(output)

@app.route('/api/kds/update_status', methods=['POST'])
def api_kds_update_status():
    data = request.json
    order = Orden.query.get(data['id'])
    if order:
        nuevo_estado = data['nuevo_estado']
        order.estado = nuevo_estado
        if nuevo_estado == 'Preparando':
            order.preparacion_at = datetime.utcnow()
        elif nuevo_estado == 'Lista_Para_Servir':
            order.listo_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"status": "success", "nuevo_estado": order.estado})
    return jsonify({"status": "error", "message": "Orden no encontrada"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
