# picasso-backend

Backend repo for the Picasso project: A curated art marketplace with a VR gallery layer.

## Security & Database Architecture

Following the latest architectural review, this project enforces strict data integrity and stateful session management constraints:

* **Database Schema Integrity:**
  * Primary keys utilize PostgreSQL's native `gen_random_uuid()` for automated, database-level generation.
  * User emails enforce case-insensitivity via the PostgreSQL `CITEXT` extension to prevent duplicate registrations.
  * Concurrent registration race conditions are explicitly handled via transaction rollbacks on database-level integrity errors.

* **Authentication & Stateful Sessions:**
  * **Passwords:** Hashed via Argon2id.
  * **Access Tokens:** 15-minute stateless JWTs.
  * **Refresh Tokens:** Opaque, 32-byte cryptographically secure strings. Only their SHA-256 hashes are stored in the database.
  * **Rotation & Replay Prevention:** Implements single-use token rotation with strict reuse detection. If an already-rotated token is presented, the system immediately revokes the entire token family to stop replay attacks.
  * **Session Tracking:** Logs IP addresses and User-Agents to support user-facing session management.