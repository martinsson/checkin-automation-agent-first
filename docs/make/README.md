# Make — création de code Igloohome

L'agent dispose d'un tool `create_door_code` qui appelle un webhook Make. Le
scénario Make crée un code PIN Igloohome via votre connexion Igloohome et
renvoie le code dans la réponse du webhook. L'adaptateur applicatif est
`src/adapters/make_door_lock.py` (`MakeDoorLockGateway`).

## Contrat requête / réponse

L'application envoie un `POST` (JSON) au webhook Make :

```json
{
  "action": "create_door_code",
  "reservation_id": 42,
  "guest_name": "Alice",
  "starts_at": "2026-07-15T13:00:00+02:00",
  "ends_at": "2026-07-18T15:00:00+02:00",
  "code_name": "Alice — resa 42"
}
```

Le scénario Make doit répondre **HTTP 200** avec un corps JSON contenant au
minimum le champ `code` :

```json
{
  "code": "43210987",
  "code_id": "pin-abc123",
  "name": "Alice — resa 42"
}
```

⚠️ Sans module « Webhook response », Make répond `Accepted` (texte brut) —
l'adaptateur lèvera alors une erreur `non-JSON`. Le module de réponse est
obligatoire.

## Mise en place du scénario

### Option A — importer le blueprint

1. Dans Make : **Scenarios → Create a new scenario → ⋯ → Import Blueprint**.
2. Importer `docs/make/igloohome-create-code.blueprint.json`.
3. Sur le module webhook (1) : créer un nouveau webhook, copier son URL.
4. Sur le module Igloohome (2) : sélectionner **votre connexion Igloohome**
   et choisir le **device/serrure** cible ; vérifier que le module
   correspond bien à « Create a PIN Code » de votre version de l'app
   Igloohome (si l'import échoue sur ce module, le remplacer à la main —
   voir Option B).
5. Activer le scénario (mode instantané).

### Option B — construction manuelle (3 modules)

1. **Webhooks → Custom webhook** — créer un webhook ; dans les réglages
   avancés, vous pouvez activer l'authentification par clé API
   (en-tête `x-make-apikey`).
2. **Igloohome → Create a PIN Code** :
   - Connection : votre connexion Igloohome
   - Device : la serrure du logement
   - Type : *Duration* (code borné dans le temps)
   - Start date : `{{1.starts_at}}` — End date : `{{1.ends_at}}`
     (Igloohome exige des heures pleines ; l'agent arrondit déjà, sinon
     utiliser `formatDate(...)` pour tronquer les minutes)
   - Access name : `{{1.code_name}}`
3. **Webhooks → Webhook response** :
   - Status : `200`
   - Header : `Content-Type: application/json`
   - Body :
     ```
     {"code": "{{2.pin}}", "code_id": "{{2.pinId}}", "name": "{{2.accessName}}"}
     ```
     (adapter les noms de champs à la sortie réelle du module Igloohome —
     lancer un « Run once » pour voir les noms exacts.)

## Configuration de l'application

Dans `.env` :

```
MAKE_IGLOOHOME_WEBHOOK_URL=https://hook.eu2.make.com/xxxxxxxxxxxxxxxx
MAKE_IGLOOHOME_API_KEY=   # optionnel — si le webhook Make exige une clé API
```

## Test de bout en bout

```bash
curl -sS -X POST "$MAKE_IGLOOHOME_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "create_door_code",
    "reservation_id": 1,
    "guest_name": "Test",
    "starts_at": "2026-07-15T13:00:00+02:00",
    "ends_at": "2026-07-15T18:00:00+02:00",
    "code_name": "Test — à supprimer"
  }'
```

La réponse doit contenir `{"code": "...", ...}`. Penser à supprimer le code
de test dans l'app Igloohome.

## Côté agent

Le tool `create_door_code` n'est déclenché que lorsqu'un événement
`door_code_request` apparaît dans le journal d'événements d'une réservation
(voir `src/prompts/agent_system.txt`). Le code créé est journalisé dans un
événement `door_code_created` ; un échec produit `door_code_failed`.
