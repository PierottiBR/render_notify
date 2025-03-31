from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import requests
import os

app = FastAPI()

# Configuración
ACCESS_TOKEN = "APP_USR-7399764412139422-042622-5c8000e5a8932bbbdae5e8d418480e65-89912040"
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000/")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base de datos temporal
usuarios_saldo = {}

@app.post("/crear_pago/")
async def crear_pago(request: Request):
    try:
        data = await request.json()
        usuario_id = data.get("usuario_id")
        monto = data.get("monto")
        email = data.get("email")
        
        if not all([usuario_id, monto, email]):
            raise HTTPException(status_code=400, detail="Se requieren usuario_id, monto y email")

        preference_data = {
            "items": [{
                "title": f"Recarga saldo - {usuario_id}",
                "quantity": 1,
                "unit_price": float(monto),
                "currency_id": "ARS"
            }],
            "payer": {"email": email},
            "payment_methods": {
                "excluded_payment_types": [{"id": "atm"}]
            },
            "back_urls": {
                "success": f"{BASE_URL}/success",
                "failure": f"{BASE_URL}/failure",
                "pending": f"{BASE_URL}/pending"
            },
            "auto_return": "approved",
            "notification_url": f"{BASE_URL}/notificacion",
            "statement_descriptor": "RECARGAS APP",
            "binary_mode": True,
            "external_reference": usuario_id
        }

        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }

        response = requests.post(
            "https://api.mercadopago.com/checkout/preferences",
            json=preference_data,
            headers=headers
        )

        if response.status_code != 201:
            error_msg = response.json().get("message", "Error en MercadoPago")
            raise HTTPException(status_code=400, detail=error_msg)

        return {
            "preference_id": response.json()["id"],
            "url_pago": response.json()["init_point"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/verificar_pago/")
async def verificar_pago(request: Request):
    try:
        data = await request.json()
        preference_id = data.get("preference_id")
        usuario_id = data.get("usuario_id")
        
        if not all([preference_id, usuario_id]):
            raise HTTPException(status_code=400, detail="Se requieren preference_id y usuario_id")

        # Paso 1: Buscar payment_id asociado
        search_headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        search_response = requests.get(
            f"https://api.mercadopago.com/v1/payments/search?preference_id={preference_id}",
            headers=search_headers
        )
        
        if search_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Error al buscar pago")

        search_data = search_response.json()
        if not search_data.get("results"):
            return {"status": "pending", "mensaje": "Pago aún no procesado"}

        payment_id = search_data["results"][0]["id"]
        
        # Paso 2: Verificar estado del pago
        payment_response = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers=search_headers
        )
        
        payment_data = payment_response.json()
        status = payment_data.get("status")
        
        if status == "approved":
            monto = payment_data.get("transaction_amount", 0)
            usuarios_saldo[usuario_id] = usuarios_saldo.get(usuario_id, 0) + monto
            
            # Ejecutar función de negocio
            try:
                from funciones_ganamos import carga_ganamos
                carga_ganamos(usuario=usuario_id, monto=monto)
            except Exception as e:
                print(f"Error en carga_ganamos: {str(e)}")

            return {
                "status": "approved",
                "payment_id": payment_id,
                "monto": monto,
                "fecha": payment_data.get("date_approved"),
                "metodo": payment_data.get("payment_type_id")
            }
        
        return {"status": status or "pending"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoints para redirección
@app.get("/success")
async def success():
    return HTMLResponse("""
    <html><body style='font-family:Arial;text-align:center;padding:50px'>
        <h1 style='color:#00a650'>✅ Pago Aprobado</h1>
        <p>El saldo se ha acreditado correctamente</p>
        <p><a href='http://localhost:8501/'>Volver a la app</a></p>
    </body></html>
    """)

@app.get("/failure")
async def failure():
    return HTMLResponse("""
    <html><body style='font-family:Arial;text-align:center;padding:50px'>
        <h1 style='color:#ff0000'>❌ Pago Fallido</h1>
        <p>El pago no pudo procesarse</p>
        <p><a href='http://localhost:8501/'>Reintentar</a></p>
    </body></html>
    """)

@app.get("/pending")
async def pending():
    return HTMLResponse("""
    <html><body style='font-family:Arial;text-align:center;padding:50px'>
        <h1 style='color:#ff8000'>⏳ Pago Pendiente</h1>
        <p>Estamos procesando tu pago</p>
        <p><a href='http://localhost:8501/'>Ver estado</a></p>
    </body></html>
    """)

# Webhook
@app.post("/notificacion/")
async def webhook(request: Request):
    try:
        payment_id = (await request.json()).get("data", {}).get("id")
        if not payment_id:
            return JSONResponse(content={"status": "invalid_data"})
        
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        payment_response = requests.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers=headers
        )
        
        payment_data = payment_response.json()
        if payment_data.get("status") == "approved":
            usuario_id = payment_data.get("external_reference")
            monto = payment_data.get("transaction_amount", 0)
            
            # Lógica de negocio aquí
            print(f"Pago aprobado para {usuario_id} por ${monto}")
            
        return JSONResponse(content={"status": "processed"})
    
    except Exception as e:
        print(f"Error en webhook: {str(e)}")
        return JSONResponse(content={"status": "error"}, status_code=500)

@app.get("/")
async def health_check():
    return {"status": "API operativa"}