# Make — création de code Igloohome

Le scénario Make crée un code PIN Igloohome (AlgoPIN « durée ») via la connexion
IglooConnect existante et renvoie le code dans la réponse du webhook.
L'adaptateur applicatif est `src/adapters/make_door_lock.py`
(`MakeDoorLockGateway`).

Ce scénario est dérivé du scénario **Igloohome → Beds24** existant (même app
custom `igloo-integration`, même connexion), simplifié pour un usage
requête/réponse synchrone : `Webhook → Set variables → Igloo AlgoPIN →
Webhook response`.

## Contrat requête / réponse

L'application envoie un `POST` (JSON) au webhook Make :

```json
{
  "action": "create_door_code",
  "purpose": "early_checkin",
  "reservation_id": 42,
  "person_name": "Alice",
  "property": "Le Fernand",
  "deviceId": "IGK3-xxxxxxxx",
  "starts_at": "2026-07-15T14:00:00",
  "ends_at": "2026-07-16T12:00:00",
  "code_name": "Alice — resa 42"
}
```

- `deviceId` : **la serrure Igloohome ciblée**. Choisi côté application à partir
  du mapping propriété→device (variable de template Beds24 n°8), voir
  `config/igloohome_devices.yaml`. Le scénario Make passe simplement
  `{{2.deviceId}}` au module Igloo.
- `starts_at` / `ends_at` : datetimes **locaux Europe/Paris** sans offset
  (`YYYY-MM-DDTHH:MM:SS`), déjà arrondis à l'heure pleine. Le module Set
  variables leur rattache l'offset Paris — **pas** de forçage 16:00/12:00
  (contrairement au scénario Beds24 : ici l'owner choisit la fenêtre exacte).
- `purpose` : `early_checkin` / `maintenance` (agent) ou `manual` (formulaire).
- `reservation_id` : `null` pour les codes manuels.
- `code_name` : libellé affiché dans l'app Igloohome (= Access Name).

Le scénario répond **HTTP 200** avec un corps JSON contenant `code` :

```json
{ "code": "43210987", "name": "Alice — resa 42" }
```

⚠️ Le module « Webhook response » est **obligatoire** : sans lui Make répond
`Accepted` (texte brut) et l'adaptateur lève une erreur `non-JSON`.

## Le scénario (blueprint)

`docs/make/igloohome-create-code.blueprint.json` reflète les identifiants
**réels** du compte (récupérés par rétro-ingénierie du scénario existant) :

| # | Module | Rôle |
|---|---|---|
| 2 | `gateway:CustomWebHook` (hook `3378292`) | reçoit le POST de l'app |
| 200 | `util:SetVariables` | `igloo_start` / `igloo_end` = `starts_at`/`ends_at` reformatés avec offset Paris |
| 4 | `app#igloo-integration-custom-app-cwjy7b:generateDurationHourlyAlgoPIN` (connexion `7418636`) | génère l'AlgoPIN ; sortie `pin` |
| 6 | `gateway:WebhookRespond` | renvoie `{"code": "{{4.pin}}", …}` |

Le webhook `3378292` et la connexion `7418636` sont ceux du **clone de test**
déjà présent dans le compte ; le blueprint les réutilise tels quels.

### Importer / mettre à jour le scénario

Sur un scénario ouvert : **⋯ (menu haut-droite) → Import blueprint**, choisir
`igloohome-create-code.blueprint.json`. L'import réutilise le webhook et la
connexion existants (aucun ré-appairage à faire). Vérifier ensuite le module
Igloo (connexion sélectionnée) puis **enregistrer** et activer le scénario.

## Configuration de l'application

Dans `.env` :

```
MAKE_IGLOOHOME_WEBHOOK_URL=https://hook.eu1.make.com/xxxxxxxxxxxxxxxx
MAKE_IGLOOHOME_API_KEY=   # optionnel — si le webhook Make exige une clé API
```

(URL = celle du module webhook `3378292`, visible dans le module Webhook.)

## Test de bout en bout

```bash
curl -sS -X POST "$MAKE_IGLOOHOME_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "create_door_code",
    "purpose": "manual",
    "person_name": "Test",
    "deviceId": "<un deviceId Igloohome réel>",
    "starts_at": "2026-07-15T14:00:00",
    "ends_at": "2026-07-15T18:00:00",
    "code_name": "Test — à supprimer"
  }'
```

La réponse doit contenir `{"code": "..."}`. Penser à supprimer le code de test
dans l'app Igloohome.

## Côté application

Deux points d'entrée créent des codes :

- **Formulaire web `/door-codes`** (derrière le login owner) — création
  manuelle pour un artisan ou une arrivée anticipée. Valeurs par défaut :
  aujourd'hui 14:00 → demain 12:00. Le PIN est affiché à l'écran.
- **Agent** : le tool `create_door_code`, déclenché par un événement
  `door_code_request` dans le journal d'une réservation. Le code créé est
  journalisé en `door_code_created` ; un échec produit `door_code_failed`.

Le `deviceId` envoyé provient du mapping `config/igloohome_devices.yaml`
(propriété → device Igloohome, source : variable de template Beds24 n°8).
