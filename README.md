Start the system in the background (Detached mode):

docker compose up -d

Rebuild and start the system: (Use this instead of the regular start command only if you added new dependencies/libraries or changed the Docker configuration)

docker compose up --build -d

Stop and remove containers: (Gracefully shuts down the database and server)

docker compose down