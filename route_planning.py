import numpy as np
import pandas as pd
import streamlit as st

def validate_legs(frame,length):
    frame=frame.dropna(how='all').copy()
    if frame.empty: raise ValueError('Add at least one segment.')
    frame[['start_km','end_km']]=frame[['start_km','end_km']].astype(float)
    if not np.isfinite(frame[['start_km','end_km']]).all().all(): raise ValueError('Distances must be finite.')
    if (frame.end_km<=frame.start_km).any() or (frame.start_km<0).any() or (frame.end_km>length+.00001).any(): raise ValueError('Each segment must have a positive length within the route.')
    if not np.allclose(frame.start_km.iloc[1:],frame.end_km.iloc[:-1],atol=.00001): raise ValueError('Segments must be in order with no gaps or overlaps.')
    if abs(frame.start_km.iloc[0])>.00001 or abs(frame.end_km.iloc[-1]-length)>.00001: raise ValueError('Segments must cover the whole route from 0 to its finish.')
    for row in frame.to_dict('records'):
        pd.Timestamp(str(row['date'])+' '+str(row['start_time']))
    return frame

def render_route_planning(route,route_id,day):
    length=float(route.distance_m.iloc[-1]/1000)
    st.subheader('Route segments / race days')
    st.caption('Distances are explicitly in km. Assign dates and start times in the selected race time zone. Each leg is simulated independently; overnight charging and automatic battery carry-over are not yet modeled.')
    count=st.number_input('Number of equal segments',1,30,3,key='leg_count')
    if st.button('Create equal segments'):
        bounds=np.linspace(0,length,count+1)
        st.session_state['legs_'+route_id]=pd.DataFrame([dict(name=f'Day {i+1}',start_km=bounds[i],end_km=bounds[i+1],date=str(day+pd.Timedelta(days=i)),start_time='09:00') for i in range(count)])
        st.session_state['legs_revision']=st.session_state.get('legs_revision',0)+1
    default=pd.DataFrame([dict(name='Day 1',start_km=0.0,end_km=length,date=str(day),start_time='09:00')])
    table=st.data_editor(st.session_state.get('legs_'+route_id,default),num_rows='dynamic',key=f'legs_editor_{route_id}_{st.session_state.get("legs_revision",0)}')
    try:
        valid=validate_legs(table,length)
    except (ValueError,TypeError) as error:
        st.warning(str(error));return
    st.session_state['legs_'+route_id]=valid
    st.download_button('Download route/day plan',valid.to_csv(index=False),'route-days.csv','text/csv')
    selected=st.selectbox('Leg to simulate',list(range(len(valid))),format_func=lambda i:str(valid.iloc[i]['name']))
    if st.button('Use selected leg'):
        st.session_state.pending_leg=valid.iloc[selected].to_dict()
        st.rerun()
