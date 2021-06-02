from esipy import EsiSecurity
from flask import Flask
from flask import Blueprint, current_app, g, session, request
from datetime import datetime
from datetime import timezone
from datetime import timedelta
from random import randint
import cfg
import database

connection = database.create_connection(
    "rollcall", "postgres", cfg.db_password, "127.0.0.1", "5432"
)
connection.autocommit = True
cursor = connection.cursor()

security = EsiSecurity(
    redirect_uri='http://51.158.104.35:5000/tokens/new',
    client_id='1922eb4bb2294e1ab3f47f15b50de475',
    secret_key= cfg.secret,
    headers={'User-Agent': cfg.agent},
)


print (security.get_auth_uri(state=randint(100000000, 999999999), scopes=['esi-fleets.read_fleet.v1']))


def create_app(config_filename=None):
    app = Flask(__name__, instance_relative_config=True)
    # app.config.from_pyfile(config_filename)
    register_blueprints(app)
    return app


root_blueprint = Blueprint('root', __name__)


@root_blueprint.route('/tokens/new')
def receive_token():
    auth_code = request.args.get("code")
    if auth_code is None:
        return current_app.response_class(
            status=400
        )
    # print(auth_code)
    tokens = security.auth(auth_code)
    # print(tokens)
    # print(tokens.get('access_token'))
    # print(tokens.get('expires_in'))
    # print(tokens.get('refresh_token'))
    api_info = security.verify()
    char_id = api_info['sub'].split(':')[-1]
    # print(char_id)

    expiration = datetime.now(timezone.utc)
    expiration += timedelta(seconds=tokens.get('expires_in'))
    insert_query = (
        "INSERT INTO commanders (char_id, access_token, expires, refresh_token, watching) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (char_id) DO UPDATE SET refresh_token = %s;"
    )
    cursor.execute(insert_query, (char_id, tokens.get('access_token'), expiration, tokens.get('refresh_token'), 0,
                                  tokens.get('refresh_token'),))
    return "Auth token added to RollCall successfully"


def register_blueprints(app):
    app.register_blueprint(root_blueprint, url_prefix='/')


app = create_app()
if __name__ == '__main__':
    app.run()