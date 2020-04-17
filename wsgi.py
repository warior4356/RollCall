import waitress
import sso
waitress.serve(sso.app, host='0.0.0.0', port=5000)