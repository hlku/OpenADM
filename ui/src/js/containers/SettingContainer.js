import React from 'react';
import RaisedButton from 'material-ui/RaisedButton';
import TextField from 'material-ui/TextField';
import { connect } from 'react-redux';
import { withHandlers, onlyUpdateForKeys, compose } from 'recompose';

const NameTextField = onlyUpdateForKeys(['value'])(
  ({ value, changeName }) => (
    <div>
      <TextField
        id="textField"
        value={value}
        onChange={(e) => changeName(e.target.value)}
      />
    </div>
  )
);

const counterIncrease = (payload = 5) => ({
  type: 'COUNTER_INCREASE',
  payload,
});

const counterDecrease = (payload = 3) => ({
  type: 'COUNTER_DECREASE',
  payload,
});

const changeName = (name) => ({
  type: 'CHANGE_NAME',
  payload: name,
});

const mapStateToProps = (state) => ({
  topology: state.topology,
  counter: state.counter,
});

const mapDispatchToProps = (dispatch) => ({
  changeName: (payload) => dispatch(changeName(payload)),
  increase: (payload) => dispatch(counterIncrease(payload)),
  decrease: (payload) => dispatch(counterDecrease(payload)),
});

const SettingContainer = compose(
  connect(mapStateToProps, mapDispatchToProps),
  withHandlers({
    handleIncrease: ({ increase }) => () => increase(5),
    handleDecrease: ({ decrease }) => () => decrease(3),
  })
)((props) => (
  <div>
    <h1>Setting</h1>
    <pre>{JSON.stringify(props, undefined, 2)}</pre>
    <RaisedButton label="增加" secondary onClick={props.handleIncrease} />
    <RaisedButton label="減少" primary onClick={props.handleDecrease} />
    <NameTextField value={props.counter.name} changeName={props.changeName} />
    <h4>name: {props.counter.name}</h4>
    <h4>count: {props.counter.count}</h4>
  </div>
  )
);

export default SettingContainer;
