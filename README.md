# Railway Client Billing Manager

MVP em Python Flask para gerenciar clientes, projetos hospedados no Railway, custos, faturas e pagamentos via Mercado Pago.

## Recursos

- Cadastro e login de clientes
- Painel do cliente com projetos, custos, faturas e histórico de pagamentos
- Painel admin com clientes, projetos, custos, faturas e pagamentos
- Webhook do Mercado Pago para atualizar pagamento automaticamente
- Visual escuro inspirado no estilo Framer: cards, bordas suaves, gráficos e destaques neon
- Preparado para deploy no Railway com PostgreSQL

## Rodando localmente

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
flask --app wsgi init-db
flask --app wsgi run
```

Acesse `http://localhost:5000`.

O usuário admin inicial é criado via variáveis `ADMIN_EMAIL` e `ADMIN_PASSWORD`.

## Deploy no Railway

1. Suba o projeto para um repositório no GitHub.
2. No Railway, crie um novo projeto a partir do repositório.
3. Adicione um serviço PostgreSQL.
4. Configure as variáveis de ambiente:
   - `FLASK_SECRET_KEY`
   - `DATABASE_URL` ou use a URL gerada pelo Railway/Postgres
   - `ADMIN_NAME`
   - `ADMIN_EMAIL`
   - `ADMIN_PASSWORD`
   - `PUBLIC_BASE_URL`, ex: `https://seu-app.up.railway.app`
   - `MERCADO_PAGO_ACCESS_TOKEN`, se for ativar pagamento real
5. Rode uma vez o comando de inicialização no Railway shell:

```bash
flask --app wsgi init-db
```

## Mercado Pago

O botão “Pagar agora” cria uma preferência de pagamento quando `MERCADO_PAGO_ACCESS_TOKEN` está configurado.
Configure o webhook no Mercado Pago apontando para:

```text
https://seu-dominio.com/webhooks/mercadopago
```

O MVP salva a referência externa da fatura e atualiza o status quando recebe notificação de pagamento aprovado.
