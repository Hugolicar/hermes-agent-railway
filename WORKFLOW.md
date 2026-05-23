# Workflow de Arquivos - Hermes + File Browser

## Regra de Ouro:
**TODO arquivo gerado, baixado ou criado deve ser salvo em `/root/.hermes/data/`**

## Procedimento:

### 1. Arquivos Pequenos (até 20MB)
- Enviar diretamente no Telegram como documento/mídia
- SALVAR também em `/root/.hermes/data/`

### 2. Arquivos Grandes (+20MB)
- Salvar em `/root/.hermes/data/`
- Informar no Telegram: "Arquivo salvo em: [nome]"
- Link do File Browser se necessário

### 3. Arquivos do Composio (/mnt/files/)
- Quando usar ferramentas Composio, copiar automaticamente para `/root/.hermes/data/`
- Manter nome original ou renomear com timestamp

### 4. Estrutura de Pastas Sugerida:
```
/root/.hermes/data/
├── instagram/          # Scraping do Instagram
├── carrosseis/         # Carrosséis gerados
├── imagens/            # Imagens geradas
├── downloads/          # Downloads gerais
├── relatorios/         # Relatórios e análises
└── temp/               # Arquivos temporários
```

## Comando Útil:
```bash
# Copiar do Composio workbench para o volume persistente
cp /mnt/files/* /root/.hermes/data/ 2>/dev/null || true
```
