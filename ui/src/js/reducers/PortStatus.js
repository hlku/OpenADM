import { handleActions } from 'redux-actions';
import Immutable from 'seamless-immutable';

const initialState = Immutable([]);

export default handleActions({
  PORT_RESP: (state, { payload }) => Immutable(payload),

}, initialState);
